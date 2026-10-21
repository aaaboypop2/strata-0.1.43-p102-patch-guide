#!/usr/bin/env python3
"""Patch Strata: parked conversations stay in RAM while it lasts and move to files on an SSD when it does not.

Usage: apply-spill-patch.py <Strata source folder>

Engine setting: STRATA_CONVERSATION_SPILL_DIR=<folder on an SSD>.  Without it nothing changes.
Optional: STRATA_CONVERSATION_SPILL_HOT_MIB=N caps the RAM the parked conversations may hold (default: no cap, the
RAM floor decides).

How it works with the variable set (Linux: unnamed files and mmap; Windows: delete-on-close files and file mappings):
  1. A conversation is parked in RAM, as before.
  2. When the RAM gate would otherwise evict parked conversations to admit a new one, the oldest RAM-held one is
     moved to files instead (its K/V segment by segment, its checkpoint state packed into one buffer), written out
     and its pages dropped.  Nothing is evicted for RAM while something can still be moved.
  3. When even that does not make room (one long conversation, little RAM), the new snapshot is captured straight
     into files.
  4. Taking a conversation back reads its files sequentially and unpacks the checkpoint state.
The cache matches a request on token ids and image keys, which always stay in RAM: no file is read to decide.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1])


def edit(path, pairs):
    p = root / path
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            sys.exit(f"{path}: the text to replace was not found:\n{old[:200]}")
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


if "ConversationSegment" in (root / "include/strata/core/conversation_buffer.hpp").read_text(encoding="utf-8"):
    sys.exit("already patched")

# ---- conversation_buffer.hpp ------------------------------------------------------------------------------------
edit("include/strata/core/conversation_buffer.hpp", [
    ("""#include <cstring>
#include <initializer_list>
#include <utility>
#include <vector>

namespace strata::core {
""", """#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <new>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#if defined(__linux__)
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#elif defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <atomic>
#endif

namespace strata::core {

// Local patch (SSD-backed parked conversations).  STRATA_CONVERSATION_SPILL_DIR names a folder on an SSD; a segment
// can then live in a file there that nothing has to clean up (Linux: unnamed, O_TMPFILE; Windows: delete-on-close),
// its size reserved up front so a full disk fails at allocation and not as a fault later.
inline const std::string& conversation_spill_dir() {
    static const std::string dir = [] {
        const char* d = std::getenv("STRATA_CONVERSATION_SPILL_DIR");
        return std::string(d != nullptr ? d : "");
    }();
    return dir;
}
// STRATA_CONVERSATION_SPILL_HOT_MIB: the RAM parked conversations may hold before the oldest moves to files
// (0 or unset: no cap of its own - the RAM floor decides, see the parking gate in generate.cpp).
inline size_t conversation_spill_hot_bytes() {
    static const size_t cap = [] {
        const char* v = std::getenv("STRATA_CONVERSATION_SPILL_HOT_MIB");
        const long long mib = v != nullptr ? std::atoll(v) : 0;
        return mib > 0 ? (size_t) mib * 1024 * 1024 : (size_t) 0;
    }();
    return cap;
}
// While one of these is alive on a thread, the segments that thread allocates are file-backed (when the folder is
// set and the segment is large enough).  Used for a snapshot that has no room in RAM and for packing.
class ConversationSpillScope {
public:
    explicit ConversationSpillScope(bool on = true) : previous_(flag()) { flag() = previous_ || on; }
    ~ConversationSpillScope() { flag() = previous_; }
    ConversationSpillScope(const ConversationSpillScope&) = delete;
    ConversationSpillScope& operator=(const ConversationSpillScope&) = delete;
    static bool active() { return flag(); }
private:
    static bool& flag() { thread_local bool on = false; return on; }
    bool previous_;
};

using ConversationFileRanges = std::vector<std::pair<void*, size_t>>;

// Write file-backed ranges to their files and drop their pages from RAM.  Neither call can change what a mapping
// holds; on a range that was unmapped meanwhile they fail and do nothing.
inline void conversation_offload(const ConversationFileRanges& ranges) {
#if defined(__linux__)
    for (const auto& [p, n] : ranges) { ::msync(p, n, MS_SYNC); ::madvise(p, n, MADV_PAGEOUT); }
#elif defined(_WIN32)
    // FlushViewOfFile starts the write to the file; VirtualUnlock on pages that are not locked takes them out of the
    // working set (it reports ERROR_NOT_LOCKED, which is the documented way to do exactly that), so they become
    // standby pages backed by the file and count as available memory.
    for (const auto& [p, n] : ranges) { ::FlushViewOfFile(p, n); ::VirtualUnlock(p, n); }
#else
    (void) ranges;
#endif
}
// The same on a thread of its own, so the request that parked the conversation does not wait for the SSD.
inline void conversation_offload_async(ConversationFileRanges&& ranges) {
    if (ranges.empty()) return;
    try {
        std::thread([r = std::move(ranges)] { conversation_offload(r); }).detach();
    } catch (...) {
        // no thread to be had: the pages stay in the page cache and the kernel writes them back on its own
    }
}

class ConversationSegment {
public:
    static constexpr size_t min_file_bytes = 1024 * 1024;
    ConversationSegment() = default;
    ConversationSegment(const ConversationSegment& other) { assign(other); }
    ConversationSegment& operator=(const ConversationSegment& other) {
        if (this != &other) { release(); assign(other); }
        return *this;
    }
    ConversationSegment(ConversationSegment&& other) noexcept
        : p_(std::exchange(other.p_, nullptr)), size_(std::exchange(other.size_, 0)),
          cap_(std::exchange(other.cap_, 0)), file_(std::exchange(other.file_, false)),
          handle_(std::exchange(other.handle_, nullptr)) {}
    ConversationSegment& operator=(ConversationSegment&& other) noexcept {
        if (this == &other) return *this;
        release();
        p_ = std::exchange(other.p_, nullptr); size_ = std::exchange(other.size_, 0);
        cap_ = std::exchange(other.cap_, 0); file_ = std::exchange(other.file_, false);
        handle_ = std::exchange(other.handle_, nullptr);
        return *this;
    }
    ~ConversationSegment() { release(); }

    uint8_t* data() { return p_; }
    const uint8_t* data() const { return p_; }
    size_t size() const { return size_; }
    size_t capacity() const { return cap_; }
    bool on_file() const { return file_; }
    void reserve(size_t n) {
        if (n <= cap_) return;
        ConversationSegment grown;
        grown.allocate(n, file_ || ConversationSpillScope::active());
        if (size_) std::memcpy(grown.p_, p_, size_);
        grown.size_ = size_;
        *this = std::move(grown);
    }
    void resize(size_t n, uint8_t value = 0) {
        if (n > cap_) reserve(n);
        if (n > size_) std::memset(p_ + size_, value, n - size_);
        size_ = n;
    }
    // Move a heap segment into a file (one segment's worth of extra memory while it copies).  False: it stays.
    bool spill() {
        if (file_ || cap_ < min_file_bytes) return false;
        ConversationSegment moved;
        moved.allocate(cap_, true);
        if (!moved.file_) return false;
        if (size_) std::memcpy(moved.p_, p_, size_);
        moved.size_ = size_;
        *this = std::move(moved);
        return true;
    }
    void file_range(ConversationFileRanges& out) const { if (file_ && cap_) out.emplace_back(p_, cap_); }
    // Ask for the segment back in one sequential read, ahead of the copy that restores the conversation.
    void prefetch() const {
#if defined(__linux__)
        if (file_ && size_) ::madvise(p_, size_, MADV_WILLNEED);
#elif defined(_WIN32)
        if (file_ && size_) {
            WIN32_MEMORY_RANGE_ENTRY range{p_, size_};
            ::PrefetchVirtualMemory(::GetCurrentProcess(), 1, &range, 0);
        }
#endif
    }

private:
    void assign(const ConversationSegment& other) {
        if (!other.cap_) return;
        allocate(other.cap_, ConversationSpillScope::active());
        if (other.size_) std::memcpy(p_, other.p_, other.size_);
        size_ = other.size_;
    }
    void allocate(size_t n, bool on_file) {
#if defined(__linux__)
        const std::string& dir = conversation_spill_dir();
        if (on_file && !dir.empty() && n >= min_file_bytes) {
            const int fd = ::open(dir.c_str(), O_TMPFILE | O_RDWR | O_CLOEXEC, 0600);
            if (fd >= 0) {
                void* m = MAP_FAILED;
                if (::posix_fallocate(fd, 0, (off_t) n) == 0)
                    m = ::mmap(nullptr, n, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
                ::close(fd);
                if (m != MAP_FAILED) { p_ = static_cast<uint8_t*>(m); cap_ = n; file_ = true; return; }
            }
        }
#elif defined(_WIN32)
        const std::string& dir = conversation_spill_dir();
        if (on_file && !dir.empty() && n >= min_file_bytes) {
            static std::atomic<unsigned long long> serial{0};
            const std::string path = dir + "\\\\strata-parked-" + std::to_string(::GetCurrentProcessId()) + "-" +
                                     std::to_string(serial.fetch_add(1)) + ".tmp";
            const HANDLE f = ::CreateFileA(path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                                           FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE, nullptr);
            if (f != INVALID_HANDLE_VALUE) {
                void* m = nullptr;
                LARGE_INTEGER size;
                size.QuadPart = (LONGLONG) n;
                if (::SetFilePointerEx(f, size, nullptr, FILE_BEGIN) && ::SetEndOfFile(f)) {
                    const HANDLE map = ::CreateFileMappingA(f, nullptr, PAGE_READWRITE, (DWORD) size.HighPart, size.LowPart, nullptr);
                    if (map != nullptr) {
                        m = ::MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, n);
                        ::CloseHandle(map);          // the view keeps the mapping alive
                    }
                }
                if (m != nullptr) { p_ = static_cast<uint8_t*>(m); cap_ = n; file_ = true; handle_ = f; return; }
                ::CloseHandle(f);                    // deletes the file
            }
        }
#else
        (void) on_file;
#endif
        p_ = static_cast<uint8_t*>(std::malloc(n));
        if (p_ == nullptr) throw std::bad_alloc();
        cap_ = n;
        file_ = false;
    }
    void release() noexcept {
        if (p_ != nullptr) {
#if defined(__linux__)
            if (file_) ::munmap(p_, cap_); else
#elif defined(_WIN32)
            if (file_) { ::UnmapViewOfFile(p_); ::CloseHandle(static_cast<HANDLE>(handle_)); } else
#endif
            std::free(p_);
        }
        p_ = nullptr; size_ = cap_ = 0; file_ = false; handle_ = nullptr;
    }
    uint8_t* p_ = nullptr;
    size_t size_ = 0, cap_ = 0;
    bool file_ = false;
    void* handle_ = nullptr;   // Windows: the delete-on-close file behind the view (unused elsewhere)
};
"""),
    ("""    void pop_back() { resize(size_ - 1); }
""", """    void pop_back() { resize(size_ - 1); }

    // SSD-backed segments (no-ops and zeros for a buffer wholly on the heap)
    size_t spill() {                       // heap segments into files; returns the bytes moved
        size_t moved = 0;
        for (auto& segment : segments_) if (segment.spill()) moved += segment.capacity();
        return moved;
    }
    void prefetch() const { for (const auto& segment : segments_) segment.prefetch(); }
    void file_ranges(ConversationFileRanges& out) const { for (const auto& segment : segments_) segment.file_range(out); }
    size_t file_bytes() const {
        size_t n = 0;
        for (const auto& segment : segments_) if (segment.on_file()) n += segment.capacity();
        return n;
    }
"""),
    ("using Segment = std::vector<uint8_t>;", "using Segment = ConversationSegment;"),
])

# ---- conversation_cache.hpp -------------------------------------------------------------------------------------
edit("include/strata/core/conversation_cache.hpp", [
    ("""    size_t bytes() const {
        return k.bytes() + v.bytes() + k_scale.bytes() + v_scale.bytes() + pooled.bytes();
    }
};

struct ConversationKvReuse {""", """    size_t bytes() const {
        return k.bytes() + v.bytes() + k_scale.bytes() + v_scale.bytes() + pooled.bytes();
    }
    size_t spill() { return k.spill() + v.spill() + k_scale.spill() + v_scale.spill() + pooled.spill(); }
    void prefetch() const { k.prefetch(); v.prefetch(); k_scale.prefetch(); v_scale.prefetch(); pooled.prefetch(); }
    void file_ranges(ConversationFileRanges& out) const {
        k.file_ranges(out); v.file_ranges(out); k_scale.file_ranges(out); v_scale.file_ranges(out); pooled.file_ranges(out);
    }
    size_t file_bytes() const {
        return k.file_bytes() + v.file_bytes() + k_scale.file_bytes() + v_scale.file_bytes() + pooled.file_bytes();
    }
};

struct ConversationKvReuse {"""),
    ("""        for (const auto& k : kv) n += k.bytes();
        return n;
    }
};

template<class Token>""", """        for (const auto& k : kv) n += k.bytes();
        return n + packed.bytes() + packed_sizes.capacity() * sizeof(size_t);
    }

    // SSD-backed parking (STRATA_CONVERSATION_SPILL_DIR).  The K/V is in ConversationBuffers, whose segments can
    // move into files one by one.  The running state of the live position and of every checkpoint (gdn, ple, tails,
    // dead, block_pos - most of a short conversation's image) is in ordinary vectors: pack() moves those bytes into
    // one more file-backed ConversationBuffer, `packed`, and empties the vectors; unpack() puts them back.  The
    // token ids and image keys the cache matches on stay where they are.  Only a parked image is ever packed:
    // ConversationCache packs when it moves an image to the SSD and unpacks in take().
    ConversationBuffer packed;
    std::vector<size_t> packed_sizes;
    template<class Self, class Fn> static void each_state(Self& image, Fn& fn) {
        auto of = [&](auto& c, auto& again) -> void {
            fn(c.gdn); fn(c.ple); fn(c.tails); fn(c.dead); fn(c.block_pos);
            for (auto& part : c.stage_parts) again(part, again);
        };
        of(image.live, of);
        for (auto& c : image.checkpoints) of(c, of);
        for (auto& s : image.stage_images) each_state(s, fn);
    }
    bool pack() {
        if (conversation_spill_dir().empty() || !packed_sizes.empty()) return false;
        size_t total = 0, count = 0;
        auto measure = [&](const std::vector<uint8_t>& v) { total += v.size(); ++count; };
        each_state(*this, measure);
        if (total < ConversationSegment::min_file_bytes) return false;
        ConversationBuffer blob;
        std::vector<size_t> sizes;
        sizes.reserve(count);
        {
            const ConversationSpillScope to_files;
            blob.resize(total);
        }
        if (blob.file_bytes() == 0) return false;    // no file behind it (disk full, ...): the image stays as it is
        size_t at = 0;
        auto store = [&](std::vector<uint8_t>& v) {
            const uint8_t* src = v.data();
            const size_t begin = at;
            blob.visit(begin, v.size(), [&](uint8_t* p, size_t n, size_t pos) { std::memcpy(p, src + (pos - begin), n); return true; });
            at += v.size();
            sizes.push_back(v.size());
            std::vector<uint8_t>().swap(v);
        };
        each_state(*this, store);
        packed = std::move(blob);
        packed_sizes = std::move(sizes);
        return true;
    }
    void unpack() {
        if (packed_sizes.empty()) return;
        size_t at = 0, i = 0;
        auto load = [&](std::vector<uint8_t>& v) {
            const size_t n = packed_sizes[i++];
            v.resize(n);
            if (n) packed.read(v.data(), at, n);
            at += n;
        };
        each_state(*this, load);
        packed = {};
        std::vector<size_t>().swap(packed_sizes);
    }
    // Everything of this image that is still in RAM goes to files.  True when something moved.
    bool spill() {
        bool moved = pack();
        for (auto& k : kv) moved = k.spill() != 0 || moved;
        for (auto& s : stage_images) {
            for (auto& k : s.kv) moved = k.spill() != 0 || moved;   // their running state went into `packed` above
        }
        return moved;
    }
    void file_ranges(ConversationFileRanges& out) const {
        packed.file_ranges(out);
        for (const auto& k : kv) k.file_ranges(out);
        for (const auto& s : stage_images) s.file_ranges(out);
    }
    void prefetch() const {
        packed.prefetch();
        for (const auto& k : kv) k.prefetch();
        for (const auto& s : stage_images) s.prefetch();
    }
    size_t file_bytes() const {
        size_t n = packed.file_bytes();
        for (const auto& k : kv) n += k.file_bytes();
        for (const auto& s : stage_images) n += s.file_bytes();
        return n;
    }
    size_t ram_bytes() const { const size_t all = bytes(), files = file_bytes(); return all > files ? all - files : 0; }
};

template<class Token>"""),
    ("""    size_t bytes() const { return bytes_ + reuse_.bytes(); }
    size_t size() const { return entries_.size(); }""", """    size_t bytes() const { return bytes_ + reuse_.bytes(); }
    // SSD-backed parking: what the parked conversations hold in files and in RAM
    size_t file_bytes() const {
        size_t n = 0;
        for (const auto& e : entries_) n += e.file_bytes();
        return n;
    }
    size_t ram_bytes() const {
        size_t n = 0;
        for (const auto& e : entries_) n += e.ram_bytes();
        return n;
    }
    size_t spilled() const { return spilled_; }
    // The parked conversation that has gone unused the longest and is still (partly) in RAM moves to files.
    // `wait`: the files are written and the pages dropped before this returns (the RAM gate reads the free RAM
    // next); otherwise on a thread of its own.  False when none could move.
    bool spill_oldest(bool wait) {
        if (conversation_spill_dir().empty()) return false;
        for (auto& e : entries_) {
            if (e.ram_bytes() < ConversationSegment::min_file_bytes) continue;
            const size_t before = e.bytes();
            if (!e.spill()) continue;
            bytes_ = bytes_ - before + e.bytes();
            ++spilled_;
            ConversationFileRanges ranges;
            e.file_ranges(ranges);
            if (wait) conversation_offload(ranges); else conversation_offload_async(std::move(ranges));
            return true;
        }
        return false;
    }
    size_t size() const { return entries_.size(); }"""),
    ("""        SavedConversation out = std::move(entries_.at(index));
        bytes_ -= out.bytes();""", """        SavedConversation out = std::move(entries_.at(index));
        bytes_ -= out.bytes();
        out.prefetch();   // SSD-backed: one sequential read now, ahead of the restore's copies
        out.unpack();"""),
    ("""        entries_.push_back(std::move(image));
        bytes_ += n;
        return true;""", """        entries_.push_back(std::move(image));
        bytes_ += n;
        // SSD-backed: an image captured straight into files is written out now, off this thread; and with a RAM cap
        // (STRATA_CONVERSATION_SPILL_HOT_MIB) the oldest RAM-held conversations move to files until the rest fits
        if (entries_.back().file_bytes() != 0) {
            ConversationFileRanges ranges;
            entries_.back().file_ranges(ranges);
            conversation_offload_async(std::move(ranges));
        }
        if (const size_t cap = conversation_spill_hot_bytes())
            while (ram_bytes() > cap && spill_oldest(false)) {}
        return true;"""),
    ("""    size_t budget_ = 0, slots_ = 0, bytes_ = 0, evictions_ = 0, superseded_ = 0;""",
     """    size_t budget_ = 0, slots_ = 0, bytes_ = 0, evictions_ = 0, superseded_ = 0, spilled_ = 0;"""),
])

# ---- generate.cpp: the parking gate -----------------------------------------------------------------------------
edit("src/program/generate.cpp", [
    ("""                while (!admit() && conversations.size() > 0 && evicted < conversations.slots() &&
                       conversations.evict_oldest())
                    ++evicted;
                if (!admit()) {
                    std::fprintf(stderr, "strata serve: conversation cache: skip parking (physical RAM admission; need %zu MiB plus %lld MiB floor, %s; evicted %zu, %zu still parked)\\n",
                                 additional >> 20, (long long) o.conversation_cache_min_free_mib, ram_note(mem).c_str(),
                                 evicted, conversations.size());
                    return true;
                }""", """                // Local patch (STRATA_CONVERSATION_SPILL_DIR): before anything is evicted for RAM, the parked
                // conversations still in RAM move to files on the SSD, oldest first, written out before the next
                // reading.  If that still leaves no room, this snapshot is captured straight into files.
                size_t moved = 0;
                bool to_files = false;
                if (!strata::core::conversation_spill_dir().empty()) {
                    while (!admit() && conversations.spill_oldest(true)) ++moved;
                    if (moved)
                        std::fprintf(stderr, "strata serve: conversation cache: moved %zu parked conversation%s from RAM to the SSD to admit this snapshot (%zu MiB plus %lld MiB floor)\\n",
                                     moved, moved == 1 ? "" : "s", additional >> 20, (long long) o.conversation_cache_min_free_mib);
                    if (!admit()) {
                        to_files = true;
                        std::fprintf(stderr, "strata serve: conversation cache: no RAM for this snapshot (%zu MiB plus %lld MiB floor, %s): it is written straight to the SSD\\n",
                                     additional >> 20, (long long) o.conversation_cache_min_free_mib, ram_note(mem).c_str());
                    }
                }
                while (!to_files && !admit() && conversations.size() > 0 && evicted < conversations.slots() &&
                       conversations.evict_oldest())
                    ++evicted;
                if (!to_files && !admit()) {
                    std::fprintf(stderr, "strata serve: conversation cache: skip parking (physical RAM admission; need %zu MiB plus %lld MiB floor, %s; evicted %zu, %zu still parked)\\n",
                                 additional >> 20, (long long) o.conversation_cache_min_free_mib, ram_note(mem).c_str(),
                                 evicted, conversations.size());
                    return true;
                }
                const strata::core::ConversationSpillScope capture_to_files(to_files);"""),
    ("""                const auto mem_after = strata::core::sample_host_memory();
                if (!strata::core::conversation_memory_admit(
                        mem_after ? std::optional<uint64_t>(mem_after->available) : std::nullopt, 0, floor)) {""",
     """                const auto mem_after = strata::core::sample_host_memory();
                if (to_files) image.spill();   // what the capture still put on the heap (the running state, small tails)
                if (!to_files && !strata::core::conversation_memory_admit(
                        mem_after ? std::optional<uint64_t>(mem_after->available) : std::nullopt, 0, floor)) {"""),
    ("""                std::fprintf(stderr, "strata serve: conversation cache: %s %zu tokens in %.1f ms; parked=%zu bytes=%zu evictions=%zu snapshot_bytes=%zu reused_kv_bytes=%zu\\n",
                             stored ? "parked" : "skipped", live.size(),
                             std::chrono::duration<double, std::milli>(Clock::now() - t0).count(),
                             conversations.size(), conversations.bytes(), conversations.evictions(), snapshot_bytes, reused_bytes);""",
     """                std::fprintf(stderr, "strata serve: conversation cache: %s %zu tokens in %.1f ms; parked=%zu bytes=%zu evictions=%zu snapshot_bytes=%zu reused_kv_bytes=%zu in_ram_bytes=%zu on_ssd_bytes=%zu moved_to_ssd=%zu\\n",
                             stored ? "parked" : "skipped", live.size(),
                             std::chrono::duration<double, std::milli>(Clock::now() - t0).count(),
                             conversations.size(), conversations.bytes(), conversations.evictions(), snapshot_bytes, reused_bytes,
                             conversations.ram_bytes(), conversations.file_bytes(), conversations.spilled());"""),
])
print("patched")

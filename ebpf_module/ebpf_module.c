#include "utils.h"

struct data_t {
    __u64 pid;
	__u64 regs[31];
	__u64 sp;
	__u64 pc;
    __u64 pstate;
    __u64 tid;
};

struct {                                                                                     
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);                                                                     
    __uint(max_entries, 1);                                                         
    __type(key, u32);                                                                    
    __type(value, struct data_t);                                                                
} event_map SEC(".maps");

struct {                                                                                       
    __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY);                                                                       
    __uint(max_entries, 1024);                                                         
    __type(key, int);                                                                    
    __type(value, __u32);                                                                
} events SEC(".maps");


static __always_inline u32 do_probe(struct pt_regs* ctx, u32 point_key) {
    __u32 zero = 0;
    struct data_t *data = bpf_map_lookup_elem(&event_map, &zero);
    if (!data) return 0; 

    data->pid = bpf_get_current_pid_tgid() >> 32;
    data->tid = (__u32)bpf_get_current_pid_tgid();

    for(int i = 0; i < 31; ++i) {
        bpf_probe_read_kernel(&data->regs[i], sizeof(data->regs[i]), &ctx->regs[i]);
    }
    bpf_probe_read_kernel(&data->sp, sizeof(data->sp), &ctx->sp);
    bpf_probe_read_kernel(&data->pc, sizeof(data->pc), &ctx->pc);
    bpf_probe_read_kernel(&data->pstate, sizeof(data->pstate), &ctx->pstate);
    bpf_perf_event_output(ctx, &events, BPF_F_CURRENT_CPU, data, sizeof(struct data_t));
    bpf_send_signal(19);
    return 0;   
}


#define PROBE(name)                          \
    SEC("uprobe/probe_##name")                     \
    int probe_##name(struct pt_regs* ctx)    \
    {                                              \
        u32 point_key = name;                       \
        return do_probe(ctx, point_key);    \
    }


PROBE(0) // Temporary breakpoint for singlestep
PROBE(1)
PROBE(2)
PROBE(3)
PROBE(4)
PROBE(5)
PROBE(6)
PROBE(7)
PROBE(8)
PROBE(9)
PROBE(10)
PROBE(11)
PROBE(12)
PROBE(13)
PROBE(14)
PROBE(15)
PROBE(16)
PROBE(17)
PROBE(18)
PROBE(19)
PROBE(20)
PROBE(21)
PROBE(22)
PROBE(23)

struct {
    __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY);
} brk_events SEC(".maps");

SEC("kprobe/perf_output_sample")
int probe_perf(struct pt_regs *ctx ) {
    __u32 zero = 0;
    struct data_t *data = bpf_map_lookup_elem(&event_map, &zero);
    if (!data) return 0; 
    data->pid = bpf_get_current_pid_tgid() >> 32;
    data->tid = (__u32)bpf_get_current_pid_tgid();
    for(int i = 0; i < 31; ++i) {
        bpf_probe_read_kernel(&data->regs[i], sizeof(data->regs[i]), &ctx->regs[i]);
    }
    data->pc = 0xffffffff;
    bpf_perf_event_output(ctx, &events, BPF_F_CURRENT_CPU, data, sizeof(struct data_t));
    bpf_send_signal(19);
    return 0;
}

// --- Linker ctor probe ---

struct linker_filter_t {
    char str[256];
    __u32 len;
};

struct linker_config_t {
    __u64 soname_offset;
};

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct linker_filter_t);
} linker_filter SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct linker_config_t);
} linker_config SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 8);
    __type(key, u32);
    __type(value, u32);
} uid_filter SEC(".maps");

struct linker_scratch_t {
    char buf[64];
};

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct linker_scratch_t);
} linker_scratch SEC(".maps");

SEC("uprobe/probe_linker")
int probe_linker(struct pt_regs *ctx) {
    __u64 uid_gid = bpf_get_current_uid_gid();
    __u32 uid = (__u32)uid_gid;
    bpf_printk("linker_probe: ENTER uid=%d", uid);

    __u32 *uid_val = bpf_map_lookup_elem(&uid_filter, &uid);
    if (!uid_val) {
        return 0;
    }

    __u32 zero = 0;
    struct linker_config_t *cfg = bpf_map_lookup_elem(&linker_config, &zero);
    if (!cfg) return 0;

    struct linker_filter_t *filter = bpf_map_lookup_elem(&linker_filter, &zero);
    if (!filter) return 0;
    if (filter->len == 0 || filter->len > 32) return 0;

    __u64 soinfo_ptr = ctx->regs[0];
    __u64 str_addr = soinfo_ptr + cfg->soname_offset;

    struct linker_scratch_t *scratch = bpf_map_lookup_elem(&linker_scratch, &zero);
    if (!scratch) return 0;
    __builtin_memset(scratch->buf, 0, sizeof(scratch->buf));

    // Android NDK uses alternate libc++ string layout:
    //   long:  { cap(8, bit0=1), size(8), data_ptr(8) }
    //   short: { size_byte(bit0=0), inline_data[23] }
    __u8 first_byte = 0;
    bpf_probe_read_user(&first_byte, 1, (void *)str_addr);
    int is_long = first_byte & 1;

    __u64 real_data_ptr = 0;
    __u64 str_size = 0;

    if (is_long) {
        bpf_probe_read_user(&str_size, sizeof(str_size), (void *)(str_addr + 8));
        bpf_probe_read_user(&real_data_ptr, sizeof(real_data_ptr), (void *)(str_addr + 16));
    } else {
        str_size = first_byte >> 1;
    }

    // Send debug event with the actual string content
    struct data_t *dbg = bpf_map_lookup_elem(&event_map, &zero);
    if (dbg) {
        __builtin_memset(dbg, 0, sizeof(*dbg));
        dbg->pid = bpf_get_current_pid_tgid() >> 32;
        dbg->tid = (__u32)bpf_get_current_pid_tgid();
        dbg->pc = 0xFFFFFFFD;
        if (is_long && real_data_ptr > 0x1000) {
            bpf_probe_read_user(&dbg->regs[0], 56, (void *)real_data_ptr);
        } else if (!is_long) {
            bpf_probe_read_user(&dbg->regs[0], 23, (void *)(str_addr + 1));
        }
        dbg->regs[28] = real_data_ptr;
        dbg->regs[29] = str_size;
        dbg->regs[30] = soinfo_ptr;
        bpf_perf_event_output(ctx, &events, BPF_F_CURRENT_CPU, dbg, sizeof(struct data_t));
    }

    // Read the string for matching
    if (is_long) {
        if (real_data_ptr > 0x1000 && str_size >= filter->len && str_size < 4096) {
            __u64 suffix_off = str_size - filter->len;
            bpf_probe_read_user(scratch->buf, 32, (void *)(real_data_ptr + suffix_off));
        } else {
            return 0;
        }
    } else {
        bpf_probe_read_user(scratch->buf, 23, (void *)(str_addr + 1));
    }

    int match = 1;
    if (filter->len >  0 && scratch->buf[ 0] != filter->str[ 0]) match = 0;
    if (filter->len >  1 && scratch->buf[ 1] != filter->str[ 1]) match = 0;
    if (filter->len >  2 && scratch->buf[ 2] != filter->str[ 2]) match = 0;
    if (filter->len >  3 && scratch->buf[ 3] != filter->str[ 3]) match = 0;
    if (filter->len >  4 && scratch->buf[ 4] != filter->str[ 4]) match = 0;
    if (filter->len >  5 && scratch->buf[ 5] != filter->str[ 5]) match = 0;
    if (filter->len >  6 && scratch->buf[ 6] != filter->str[ 6]) match = 0;
    if (filter->len >  7 && scratch->buf[ 7] != filter->str[ 7]) match = 0;
    if (filter->len >  8 && scratch->buf[ 8] != filter->str[ 8]) match = 0;
    if (filter->len >  9 && scratch->buf[ 9] != filter->str[ 9]) match = 0;
    if (filter->len > 10 && scratch->buf[10] != filter->str[10]) match = 0;
    if (filter->len > 11 && scratch->buf[11] != filter->str[11]) match = 0;
    if (filter->len > 12 && scratch->buf[12] != filter->str[12]) match = 0;
    if (filter->len > 13 && scratch->buf[13] != filter->str[13]) match = 0;
    if (filter->len > 14 && scratch->buf[14] != filter->str[14]) match = 0;
    if (filter->len > 15 && scratch->buf[15] != filter->str[15]) match = 0;
    if (filter->len > 16 && scratch->buf[16] != filter->str[16]) match = 0;
    if (filter->len > 17 && scratch->buf[17] != filter->str[17]) match = 0;
    if (filter->len > 18 && scratch->buf[18] != filter->str[18]) match = 0;
    if (filter->len > 19 && scratch->buf[19] != filter->str[19]) match = 0;
    if (filter->len > 20 && scratch->buf[20] != filter->str[20]) match = 0;
    if (filter->len > 21 && scratch->buf[21] != filter->str[21]) match = 0;
    if (filter->len > 22 && scratch->buf[22] != filter->str[22]) match = 0;
    if (filter->len > 23 && scratch->buf[23] != filter->str[23]) match = 0;
    if (filter->len > 24 && scratch->buf[24] != filter->str[24]) match = 0;
    if (filter->len > 25 && scratch->buf[25] != filter->str[25]) match = 0;
    if (filter->len > 26 && scratch->buf[26] != filter->str[26]) match = 0;
    if (filter->len > 27 && scratch->buf[27] != filter->str[27]) match = 0;
    if (filter->len > 28 && scratch->buf[28] != filter->str[28]) match = 0;
    if (filter->len > 29 && scratch->buf[29] != filter->str[29]) match = 0;
    if (filter->len > 30 && scratch->buf[30] != filter->str[30]) match = 0;
    if (filter->len > 31 && scratch->buf[31] != filter->str[31]) match = 0;
    if (!match) return 0;

    struct data_t *data = bpf_map_lookup_elem(&event_map, &zero);
    if (!data) return 0;
    data->pid = bpf_get_current_pid_tgid() >> 32;
    data->tid = (__u32)bpf_get_current_pid_tgid();
    data->pc = 0xFFFFFFFE;
    bpf_perf_event_output(ctx, &events, BPF_F_CURRENT_CPU, data, sizeof(struct data_t));
    bpf_send_signal(19);
    return 0;
}

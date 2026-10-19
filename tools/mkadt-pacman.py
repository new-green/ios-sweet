#!/usr/bin/env python3
"""Build the Apple Device Tree that the PACMAN (MT6886) xnu needs to boot.

Forked from ios-sweet's mkadt.py. Every MMIO address below was measured from
the device (read-only) and/or extracted from the stock-firmware DTB:

  device      : Nothing Phone (2a), model A142, codename Pacman, SoC MT6886
  firmware    : Pacman_B4.1-260928-1157 (= build BP2A.250605.031.A3)
  DTB source  : vendor_boot.img (main DTB, 282 KB) + dtbo.img overlays
  iomem/cmdline: /proc/iomem, /proc/cmdline via KernelSU root, read-only

See ../../../DONANIM-HARITASI.md for the full measured map.

Usage:  mkadt-pacman.py OUT.adt [--dump] [--qemu] [--highmem]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# --- Measured device constants (MT6886 / Pacman) -----------------------------

# PACMAN (MT6886) machine config in xnu/patches/0083 registers the
# 'nothing,pacman' platform expert match; the current pre-PACMAN sweet kernel
# will stop at IOPlatformExpert.cpp:2319 until that patch is built, which is
# the expected result and what tools/qemu-sweet.py reports as a panic.
PACMAN_PLATFORM_MATCH = 'nothing,pacman'

# Kernel is loaded at 0x40000000 and XNU must be told the same base; /proc/iomem
# shows "Kernel code : 0x40010000-0x4287ffff" on the stock Android kernel.
# NOTE: the stock DTB /memory node only declares 1 GiB (0x40000000..0x80000000);
# MediaTek widens the real map at runtime via mblock. The real map is fragmented
# (see DONANIM-HARITASI.md §2), so the shim must hand XNU one contiguous window.
PHYS_BASE = 0x40000000
# Window offered to XNU: the low contiguous region up to the first big
# reserved hole is too small; use the low 3 GiB which iomem shows as
# predominantly System RAM with the framebuffer carved out separately.
MEM_SIZE = 0xC0000000  # 0x40000000..0xFFFFFFFF = 3 GiB

# On Pacman DRAM actually extends to ~0x340000000 (12+ GiB). High bank for
# --highmem, mirroring sweet's option (upper DDR only).
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0x240000000  # ~9 GiB measured upper region

# /arm-io children are relative to this; must not be 0 (serial_init bails).
SOC_BASE = 0x01000000

# GICv3, from the main DTB (arm,gic-v3):
#   reg = <0x0 0x0c400000 0x0 0x40000  0x0 0x0c440000 0x0 0x200000>
GICD, GICD_SIZE = 0x0C400000, 0x40000
GICR, GICR_SIZE = 0x0C440000, 0x200000

# LK continuous-splash framebuffer, read from /chosen/atag,videolfb-* (root):
#   fb_base_h = 0x00000000, fb_base_l = 0xfe06c000  ->  0xfe06c000
#   vramSize  = 0x01e50000 (31.7 MB)
# /proc/iomem confirms "fe06c000-ffffefff : reserved", i.e. still resident.
FB, FB_SIZE = 0xFE06C000, 0x01E50000
# Panel rm692h5_fhdp_dsi_cmd_boe_raydium, native 1084x2412 (density 420).
# We present the iPhone X canvas (1125x2436) to the userland like sweet does.
FB_W, FB_H = 1084, 2412
FB_STRIDE = FB_W * 4

# UFS host: ufshci@112b0000 "mediatek,mt8183-ufshci", reg 0x2300, SPI 169.
# /proc/iomem confirms "112b0000-112b22ff : 112b0000.ufshci".
UFS, UFS_SIZE = 0x112B0000, 0x2300

# arm,armv8-timer clock-frequency = 0xc65d40 = 13 MHz.
TIMEBASE = 13000000
# Timer PPIs from the DTB interrupts list:
#   PPI 13 secure EL1 phys, PPI 14 non-secure EL1 phys,
#   PPI 11 secure EL1 virt, PPI 10 non-secure EL1 virt.
# XNU uses the non-secure EL1 virtual timer -> 10.
VTIMER_INTID = 10
PTIMER_INTID = 14

# Synthetic ECID for this port ('PACM' + 1); NOT the phone's real one.
PACMAN_ECID = 0x5041434D414E0001

# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76) for host-side smoke tests.
# QEMU places its own DTB and our loader stub at the base of RAM (0x40000000),
# so the kernel-managed window is shifted up to 0xb0000000 (as sweet does) to
# avoid clobbering them. Everything else (GIC, timer) matches QEMU virt.
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080A0000, 62500000
QEMU_VTIMER_INTID = 27
QEMU_PHYS_BASE, QEMU_MEM_SIZE = 0xB0000000, 0x30000000

BOOT_MANIFEST_HASH = hashlib.sha384(b'pacman boot manifest').digest()

# /product comes from the real iPhone X (D22AP) tree, an Apple artifact kept
# out of git. Extract it from the IPSW (see ios-sweet NOTES.md).
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without syscfg placeholders."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False):
    """qemu=True: same tree but with QEMU virt's GIC addresses and CNTFRQ."""
    if qemu:
        phys_base, mem_size = QEMU_PHYS_BASE, QEMU_MEM_SIZE
    else:
        phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    vtimer = QEMU_VTIMER_INTID if qemu else VTIMER_INTID
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('pacman-shim')),
        ('system-firmware-version', cstr('pacman-shim')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless it
        # exists. Same fixed stand-in value used by sweet.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts without it; A11+ trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt screen class. Framebuffer is 1084x2412; presenting the
        # iPhone X 1125x2436 canvas @3x -> 375x812 points (same class as sweet).
        ('display-scale', u32(3)),
        # IOPlatformUUID source. Synthetic ECID; chip-id of the A11 the
        # iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', PACMAN_ECID)),
        ('chip-id', u32(0x8015)),
        # Dev-fused, undemoted board (tuple 0,0,1,1) -> mobileactivationd takes
        # the native "Hactivation" branch (Activated, no SEP/activation record).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # PACMAN SMP: six Cortex-A510 (cluster 0) + two Cortex-A715 (cluster 1).
    # tick is homogeneous CLUSTER_TYPE_SMP across two clusters, exactly like
    # sweet; only the per-core MPIDR reg and cluster-id differ.
    #   DTB cpu@0..5  compatible "arm,cortex-a55"  reg 0x000..0x500  -> A510
    #   DTB cpu@6..7  compatible "arm,cortex-a78"  reg 0x600/0x700  -> A715
    #   sysfs confirms physical_package_id 0 for 0-5, 1 for 6-7.
    cpu_nodes = []
    for core in range(1 if qemu else 8):
        cluster = 0 if core < 6 else 1
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('arm,cortex-a510' if core < 6 else 'arm,cortex-a715') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),  # MPIDR Aff1:Aff0 as the DTB declares it
            ('cpu-id', u32(core)),
            ('cluster-id', u32(cluster)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core if cluster == 0 else core - 6)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            # A715 big cores run higher; A510 little cores lower. Descriptive.
            ('clock-frequency', u32(2600000000 if core >= 6 else 2000000000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(3200000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(vtimer)),
        ('reg', u64(gicd - SOC_BASE, GICD_SIZE, gicr - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('pacman-io')),
        ('compatible', cstr('pacman-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('pacman,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])])

    fbcon = node('pacman-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    # bsd/dev/pacman_ufs.c: the MediaTek UFS host left running by LK.
    ufs = node('pacman-ufs', [
        ph(),
        ('compatible', cstr('mediatek,mt8183-ufshci')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    # Identity of the iPhone X whose userland runs here. 'mediatek,mt6886' and
    # 'nothing,pacman' stay for PacmanPlatformExpert matching.
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('nothing,pacman') + cstr('mediatek,mt6886') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if qemu else [ufs]))


if __name__ == '__main__':
    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv)
    blob = adt.serialize(root)
    open(sys.argv[1], 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes', file=sys.stderr)

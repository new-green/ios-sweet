# Port notları — MT6886 (Nothing Phone (2a), "pacman")

Bu dosya, sweet (SM7150) tabanlı XNU'yu **MediaTek MT6886**'ya portlarken
gereken farkları belgeler. Her madde donanımdan **ölçülmüş** bir gerçeğe
dayanır; kaynaklar yerel proje dosyalarıdır (`DONANIM-HARITASI.md`).

## Ölçülmüş donanım (cihazdan, salt okuma)

| Alan | sweet (SM7150) | **pacman (MT6886)** |
|---|---|---|
| Kernel yükleme tabanı | `0xb0000000` | **`0x40000000`** |
| Framebuffer | `0x9c000000` (23 MB) | **`0xfe06c000`** (31.7 MB) |
| GICD | `0x17a00000` | **`0x0c400000`** |
| GICR | `0x17a60000` | **`0x0c440000`** |
| Sanal timer PPI | 19 | **10** |
| Fiziksel timer PPI | 18 | **14** |
| Timer frekansı | — | **13 MHz** (`0xc65d40`) |
| UFS denetleyici | `0x01d84000` | **`0x112b0000`** |
| UFS IRQ | — | SPI **169** |
| CPU | 6×A55 + 2×A76 (8/8 16K) | **6×A510 + 2×A715** (8/8 16K) |
| MPIDR `reg` | `0x000-0x500`, `0x600/0x700` | **`0x000-0x500`, `0x600/0x700`** (aynı!) |
| Panel | video mode | **DSI command mode** (`rm692h5`) |
| PMIC | Qualcomm SPMI | **MediaTek mt6363/mt6368** |
| EL2 | serbest | **KVM protected mode** (`kvm-arm.mode=protected`) |
| `fastboot boot` | çalışır | **ÇALIŞMAZ** (`unknown command`) |

## Yama katmanları

### Cihazdan bağımsız (sweet yamaları 0001–0079)
Bunlar XNU'yu Apple olmayan ARM64 donanımda çalıştırmanın genel işi:
pmap, asm, boot_args, ADT, AMFI/sandbox kapatma, HFS+ kökü, IOKit
HID servisleri. Yeni bir cihazda **çoğu aynen kalır**.

### Cihaza özel (yeni yamalar 0082+)
Bunlar donanım sabitlerini taşır. Pacman'de değişmesi gerekenler
yukarıdaki tabloda. Sıralama:

- `0082-pacman-gic-base.patch` — GICD/GICR adresleri, PPI partition'ları
- `0083-pacman-timer-ppi.patch` — vtimer 10, ptimer 14, 13 MHz
- `0084-pacman-mem-base.patch` — kernel `0x40000000`, dağınık DRAM
  haritasını XNU'ya sürekli pencere olarak sun
- `0085-pacman-fb-base.patch` — framebuffer `0xfe06c000`, 1084×2412
- `0086-pacman-ufs.patch` — MediaTek UFS (`0x112b0000`, SPI 169)
- `0087-pacman-cpu-topology.patch` — A510/A715 cluster doğrulaması
- `0088-pacman-el2-protected.patch` — EL2'den EL1'e iniş

## Açık sorular (henüz ölçülmedi)

1. **Gerçek MPIDR bit düzeni** — Linux `reg`'i normalize ediyor; XNU'nun
   PSCI `CPU_ON` çağrısına hangi MPIDR'ı vereceği shim'de log'lanmalı.
2. **Dağınık DRAM haritası** — MediaTek `0x40000000`-`0x33fffffff` arasını
   onlarca blok halinde sunuyor. XNU'ya nasıl aktarılacağı tasarlanmalı.
3. **DSI command mode** — framebuffer belleğine yazmak ekrana yansır mı?
   Yoksa RDMA frame gönderimi mi gerekir?

## Kaynak

Bu notlar `DONANIM-HARITASI.md` ve `CIHAZ-RAPORU.md` dosyalarından
türetilmiştir. Cihaza hiçbir yazma işlemi yapılmadı.

---
title: 给 AI Agent 造一台电脑：虚拟机沙箱设计与学习路线
kicker: 设计指南
deck: 从零理解如何为 agent 构建可运行 macOS / Linux 客机、带完整 GUI 交互和网络控制的虚拟机平台：每一层有哪些做法、怎么取舍、按什么顺序学。
meta:
  - macOS · Linux 客机
  - GUI 交互
  - 网络控制
  - 学习路线
footer: 文中代码均为示意，用于说明各层的形态；API 名称参考 Virtualization.framework、QEMU、nftables、mitmproxy 的公开文档。写于 2026 年 11 月。
index: index.html
updated: 2026-11-19
created: 2026-11-19
---

这篇设计指南写给对虚拟化完全不熟、之后打算让 AI 写代码的人。要做的系统是一个给 AI agent 分配“电脑”的平台：每个任务拿到一台独立的 macOS 或 Linux 虚拟机，agent 能看屏幕、点鼠标、敲键盘、跑命令，而它的每一次联网都在你的控制之下；任务结束，机器就销毁。

本文不展开每个 API，只讲每一层有哪些做法、各自的代价，以及哪些地方需要你来做决定。读完以后，你应该能看懂 AI 给出的方案，也能判断它选得对不对。文中的代码都是示意，用来说明“这一层大概长什么样”，不能直接运行。

- **架构**：agent → 控制 API → 编排器 → VM 后端；网络单独经过一道闸门
- **平台**：要跑 macOS 客机，就必须用 Mac 当宿主机，并使用 Virtualization.framework
- **GUI**：像素和键鼠从外面走 VNC；界面结构和 shell 由客机里的守护进程负责
- **网络**：默认全部拒绝，所有出站流量都经过宿主机上的代理，规则在宿主机上执行
- **生命周期**：黄金镜像 → 写时复制克隆 → 用完即弃
- **学习路线**：分 5 个阶段，每个阶段都交付一个能跑、能验收的东西

## 全景：你要造的不是虚拟机，而是“给 agent 租电脑”的系统

虚拟机本身是现成的：macOS 上有 Apple 的 Virtualization.framework，Linux 上有 KVM/QEMU。你不需要、也不应该自己写 hypervisor。你要做的是它上面的四层：

- **控制 API**：把“操作一台电脑”变成 agent 能调用的工具，例如截图、点击、输入和执行命令。
- **编排器**：管理一批虚拟机，负责分配、回收、排队、超时，以及下发网络策略。
- **网络闸门**：决定每台虚拟机能访问哪些地址，并记录每一次访问。
- **镜像与快照**：保证每个任务都从同一个干净的状态开始，并且能在几秒内拿到机器。

这四层决定了产品好不好用、安不安全，也是你和“直接用 UTM 开一台虚拟机”拉开差距的地方。

```svg title="图 1：整体架构——虚拟化是现成的，你要做的是上面四层" tone="light"
<svg viewBox="0 0 760 425" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif" font-size="13">
  <defs><marker id="a1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5b6475"/></marker></defs>
  <rect x="0" y="0" width="760" height="425" fill="#ffffff"/>
  <rect x="250" y="10" width="260" height="40" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="380" y="35" text-anchor="middle" fill="#1f2430">AI Agent（模型 + 执行循环）</text>
  <line x1="380" y1="50" x2="380" y2="70" stroke="#5b6475" marker-end="url(#a1)"/>
  <rect x="170" y="72" width="420" height="44" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="380" y="99" text-anchor="middle" fill="#1f2430">控制 API：screenshot · click · type · shell · reset</text>
  <line x1="380" y1="116" x2="380" y2="128" stroke="#5b6475" marker-end="url(#a1)"/>
  <rect x="170" y="130" width="420" height="44" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="380" y="157" text-anchor="middle" fill="#1f2430">编排器：VM 池 · 调度 · 快照 · 策略下发 · 录像日志</text>
  <line x1="270" y1="174" x2="190" y2="198" stroke="#5b6475" marker-end="url(#a1)"/>
  <line x1="490" y1="174" x2="500" y2="198" stroke="#5b6475" marker-end="url(#a1)"/>
  <rect x="20" y="200" width="340" height="130" rx="10" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="190" y="222" text-anchor="middle" fill="#1f2430" font-weight="600">Mac 宿主机 · Virtualization.framework</text>
  <rect x="40" y="240" width="140" height="70" rx="8" fill="#ffffff" stroke="#c3cad8"/>
  <text x="110" y="270" text-anchor="middle" fill="#1f2430">macOS 客机</text>
  <text x="110" y="290" text-anchor="middle" fill="#6b7280" font-size="11">每台宿主机最多 2 台</text>
  <rect x="200" y="240" width="140" height="70" rx="8" fill="#ffffff" stroke="#c3cad8"/>
  <text x="270" y="270" text-anchor="middle" fill="#1f2430">Linux 客机</text>
  <text x="270" y="290" text-anchor="middle" fill="#6b7280" font-size="11">ARM 版</text>
  <rect x="400" y="200" width="200" height="130" rx="10" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="500" y="222" text-anchor="middle" fill="#1f2430" font-weight="600">Linux 宿主机 · KVM/QEMU</text>
  <rect x="420" y="240" width="160" height="70" rx="8" fill="#ffffff" stroke="#c3cad8"/>
  <text x="500" y="270" text-anchor="middle" fill="#1f2430">Linux 客机 × N</text>
  <text x="500" y="290" text-anchor="middle" fill="#6b7280" font-size="11">规模化阶段再加</text>
  <rect x="625" y="130" width="120" height="44" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="685" y="157" text-anchor="middle" fill="#1f2430">互联网</text>
  <rect x="625" y="200" width="120" height="130" rx="10" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="685" y="225" text-anchor="middle" fill="#1f2430" font-weight="600">网络闸门</text>
  <text x="685" y="255" text-anchor="middle" fill="#1f2430" font-size="12">出站代理白名单</text>
  <text x="685" y="280" text-anchor="middle" fill="#1f2430" font-size="12">受控 DNS</text>
  <text x="685" y="305" text-anchor="middle" fill="#1f2430" font-size="12">防火墙</text>
  <line x1="685" y1="200" x2="685" y2="176" stroke="#5b6475" marker-end="url(#a1)"/>
  <line x1="600" y1="265" x2="623" y2="265" stroke="#5b6475" marker-end="url(#a1)"/>
  <path d="M190,330 L190,348 L685,348 L685,332" fill="none" stroke="#5b6475" marker-end="url(#a1)"/>
  <rect x="20" y="365" width="580" height="44" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="310" y="392" text-anchor="middle" fill="#1f2430">镜像仓库 + 快照存储：黄金镜像 → 克隆 → 用完即弃</text>
</svg>
```

### 一次任务从开始到结束经过哪些步骤？

先在脑子里过一遍完整流程，后面每一节讲的都是其中一步怎么做。注意第 2 步：网络策略要在 agent 拿到机器**之前**就生效，而不是等 agent 开始工作后再补上。

```text title="一次任务的生命周期"
1. 调度：编排器从预热池取一台 macOS VM（池子空了就从黄金镜像克隆一台再开机）
2. 下发策略：为这台 VM 生成网络规则，例如只允许 github.com 和 pypi.org
3. 交给 agent：返回 session_id 和可用的工具列表
4. 循环：agent 调 screenshot → 模型决定动作 → click / type / shell → 再截图……
5. 收尾：拷出产物文件、录屏、操作日志和网络日志
6. 销毁：删除克隆出的磁盘，释放这台宿主机的名额
```

## 只需要先懂 6 个概念，其余的边做边学

虚拟化的资料很多，但做这个项目一开始只需要下面 6 个概念。每个概念右侧一列说明它和这个项目的关系，读资料时可以只看这些部分。

| 概念 | 一句话解释 | 在这个项目里意味着什么 |
| --- | --- | --- |
| Hypervisor（虚拟机监控器） | 把一台物理机切成多台虚拟机的软件 | 你选择的后端：Virtualization.framework，或 KVM/QEMU |
| 硬件加速 | 由 CPU 直接执行客机指令（Apple 的 HVF、Linux 的 KVM），比纯软件模拟快一两个数量级 | 客机的 CPU 架构要和宿主机一致：Apple Silicon 上只能高效运行 ARM 版的 macOS 和 Linux |
| 虚拟设备 / virtio | 虚拟机里的网卡、磁盘、显卡和键鼠都由 hypervisor 模拟；virtio 是一套高效的标准虚拟设备 | GUI 交互和网络控制，本质上都是在这些虚拟设备上动手 |
| 磁盘镜像与写时复制 | 客机的硬盘就是宿主机上的一个文件；克隆时只记录与原文件的差异 | 能在几秒内复制出一台干净的机器，“用完即弃”才做得到 |
| 宿主机与客机之间的通道 | SSH、virtio-vsock（一种不经过网络的宿主机与客机之间的套接字）、共享目录 | 用来下发命令、传文件，以及和客机里的守护进程通信 |
| 容器 vs 虚拟机 | 容器和宿主机共用一个内核，虚拟机有自己独立的内核 | 容器启动快但隔离弱，而且跑不了 macOS；agent 在机器里有 root 权限时，优先用虚拟机 |

> 理解了虚拟设备，就能看懂后面所有的设计取舍：对客机的控制要么**从外面**通过虚拟设备做（不需要客机配合，客机也绕不过去），要么**在里面**装程序做（能力更强，但客机里的 agent 理论上能干扰它）。这个区分在 GUI 和网络两节会反复出现。

## 平台选型：要跑 macOS 客机，就必须有 Mac

### macOS 客机只有一条路：Apple 硬件 + Virtualization.framework

Apple 的软件许可只允许在 Apple 品牌的硬件上虚拟化 macOS，而且每台宿主机同时最多运行两台 macOS 虚拟机，框架本身也会强制这个上限。所以 **macOS 容量 = Mac 数量 × 2**。云上可以租 AWS EC2 Mac 这类整机，但通常按整台机器计费，并且有最短租期。

Virtualization.framework 是 Apple 官方提供的 Swift API，Tart、UTM、Lume 等工具都基于它。它的思路是用一个配置对象描述整台机器的硬件，每一项都是一个设计决策点：网卡挂哪种 attachment 决定了你能把网络管到多细，显卡和键鼠决定了 GUI 怎么交互，socket 设备就是 vsock 通道。

> 同一个框架也能跑带图形界面的 ARM 版 Linux 客机。所以在原型阶段，一台 Apple Silicon Mac 就能把两种客机都跑起来。

```swift title="示意：用 Virtualization.framework 描述一台 macOS 虚拟机"
import Virtualization

let config = VZVirtualMachineConfiguration()
config.cpuCount = 4
config.memorySize = 8 * 1024 * 1024 * 1024
config.bootLoader = VZMacOSBootLoader()
config.platform = macPlatform   // 硬件型号、机器标识、辅助存储，安装时生成

// 磁盘：宿主机上的一个文件
let disk = try VZDiskImageStorageDeviceAttachment(url: diskURL, readOnly: false)
config.storageDevices = [VZVirtioBlockDeviceConfiguration(attachment: disk)]

// 网卡：attachment 的类型决定了网络能控制到什么程度（见第 05 节）
let nic = VZVirtioNetworkDeviceConfiguration()
nic.attachment = VZNATNetworkDeviceAttachment()
config.networkDevices = [nic]

// 显示与输入：固定分辨率，后面截图坐标才好算
let gpu = VZMacGraphicsDeviceConfiguration()
gpu.displays = [VZMacGraphicsDisplayConfiguration(widthInPixels: 1280, heightInPixels: 800, pixelsPerInch: 80)]
config.graphicsDevices = [gpu]
config.keyboards = [VZUSBKeyboardConfiguration()]
config.pointingDevices = [VZMacTrackpadConfiguration()]

// vsock：宿主机和客机内守护进程之间的通道
config.socketDevices = [VZVirtioSocketDeviceConfiguration()]

try config.validate()
let vm = VZVirtualMachine(configuration: config)
```

### Linux 客机有四种隔离强度，怎么选？

Linux 的选择多得多，区别主要在隔离强度和启动速度。agent 会执行模型生成的任意命令，所以默认要按“客机里跑的是不可信代码”来设计。

| 方案 | 隔离 | GUI 怎么来 | 启动 | 适合 |
| --- | --- | --- | --- | --- |
| Docker + Xvfb + VNC | 弱，和宿主机共用内核 | 客机里的虚拟屏幕 Xvfb | 1 秒内 | 本地原型、可信任务 |
| gVisor / Kata Containers | 中到强 | 同上 | 秒级 | 想用容器生态，又需要更强隔离 |
| Firecracker 微虚拟机 | 强 | 没有显示设备，只能在里面跑 Xvfb + VNC | 秒级 | 大规模 Linux 沙箱，E2B 一类服务就这样做 |
| QEMU/KVM 完整虚拟机 | 强 | 有虚拟显卡，可以直接输出 VNC/SPICE | 十秒级，用快照可以加速 | 需要完整桌面，或者希望和 macOS 方案保持一致 |

> 在云服务器上用 KVM，需要裸金属实例或支持嵌套虚拟化的实例，选机型前先确认这一点。

### 推荐：原型全放 Mac，后端从第一天就抽象出来

原型阶段用一台 Apple Silicon Mac，直接借用 Tart 或 Lume，同时跑 macOS 和 Linux 客机。规模变大后，把 Linux 迁到 Linux 服务器（QEMU/KVM 或 Firecracker），macOS 继续由 Mac 集群承担。

为了以后迁得动，编排器从第一天起就只依赖一个后端接口，而不是直接调用某个工具。让 AI 写代码时，先把这个接口定下来。下面第二块是同样一台 Linux 机器在 QEMU 里的启动参数，每一行都对应接口里的一项能力。

```ts title="示意：编排器只依赖这个接口"
interface VmBackend {
  create(image: string, spec: { cpu: number; memoryGb: number; display: [number, number] }): Promise<VmId>;
  start(id: VmId): Promise<void>;
  snapshot(id: VmId, name: string): Promise<void>;
  destroy(id: VmId): Promise<void>;
  // GUI 与控制通道：每个后端都返回统一格式的连接信息
  endpoints(id: VmId): Promise<{ vnc: string; agent: string }>;
  // 网络：后端负责把同一份策略落到自己的网络实现上
  applyNetworkPolicy(id: VmId, policy: NetworkPolicy): Promise<void>;
}
```

```bash title="示意：QEMU/KVM 启动一台 Linux 桌面客机"
# 从黄金镜像创建一个写时复制的“差异盘”，只记录这次任务的改动
qemu-img create -f qcow2 -b ubuntu-desktop-golden.qcow2 -F qcow2 task-123.qcow2

qemu-system-x86_64 -enable-kvm -smp 4 -m 4096 \
  -drive file=task-123.qcow2,if=virtio \
  -netdev tap,id=n0,ifname=tap-123,script=no,downscript=no \
  -device virtio-net-pci,netdev=n0 \
  -device virtio-vga -display none -vnc 127.0.0.1:23 \
  -device vhost-vsock-pci,guest-cid=123 \
  -qmp unix:/run/vm/123.qmp,server=on,wait=off
# tap-123：这台 VM 专属的网卡，宿主机防火墙针对它写规则
# -vnc：GUI 从虚拟显卡直接输出，不需要客机配合
# vsock：和客机内守护进程通信；qmp：宿主机控制 VM（暂停、快照、注入按键）
```

## GUI 交互：像素和键鼠从外面拿，界面结构从里面拿

agent 操作电脑需要三种能力：**看**（截图）、**动**（鼠标和键盘）、**懂**（界面结构，也就是可访问性树，它告诉模型“这里有一个叫‘提交’的按钮”）。前两种是必需的，现在的 computer-use 模型主要靠截图加坐标工作；第三种是增强，能提高定位的准确率，也方便你写自动化断言。

这些能力有两条获取路径（图 2）。推荐两条都做：路径 A 是所有客机共用的保底能力，路径 B 补上它做不到的事。

```svg title="图 2：GUI 交互的两条路径" tone="light"
<svg viewBox="0 0 720 300" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif" font-size="13">
  <defs><marker id="a2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#5b6475"/></marker></defs>
  <rect x="0" y="0" width="720" height="300" fill="#ffffff"/>
  <rect x="20" y="40" width="150" height="220" rx="10" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="95" y="140" text-anchor="middle" fill="#1f2430" font-weight="600">宿主机</text>
  <text x="95" y="162" text-anchor="middle" fill="#1f2430">控制服务</text>
  <rect x="330" y="20" width="370" height="260" rx="10" fill="#f5f7fb" stroke="#9aa3b5" stroke-dasharray="6 4"/>
  <text x="515" y="40" text-anchor="middle" fill="#6b7280" font-size="12">客机（按不可信对待）</text>
  <rect x="350" y="55" width="330" height="105" rx="8" fill="#ffffff" stroke="#c3cad8"/>
  <text x="515" y="80" text-anchor="middle" fill="#1f2430" font-weight="600">客机内守护进程（开机自启）</text>
  <text x="515" y="106" text-anchor="middle" fill="#1f2430" font-size="12">Linux：xdotool · AT-SPI · shell</text>
  <text x="515" y="128" text-anchor="middle" fill="#1f2430" font-size="12">macOS：CGEvent · ScreenCaptureKit · AX API</text>
  <text x="515" y="150" text-anchor="middle" fill="#6b7280" font-size="11">截图 · 输入 · 可访问性树 · 命令 · 文件</text>
  <rect x="350" y="190" width="330" height="70" rx="8" fill="#ffffff" stroke="#c3cad8"/>
  <text x="515" y="220" text-anchor="middle" fill="#1f2430" font-weight="600">虚拟显卡 · 虚拟键盘 / 鼠标</text>
  <text x="515" y="242" text-anchor="middle" fill="#6b7280" font-size="11">由 hypervisor 提供，客机无法关掉</text>
  <line x1="172" y1="105" x2="348" y2="105" stroke="#5b6475" marker-start="url(#a2)" marker-end="url(#a2)"/>
  <text x="260" y="96" text-anchor="middle" fill="#1f2430" font-size="12">路径 B：vsock / SSH</text>
  <text x="260" y="124" text-anchor="middle" fill="#6b7280" font-size="11">结构化信息，需要客机配合</text>
  <line x1="172" y1="225" x2="348" y2="225" stroke="#5b6475" marker-start="url(#a2)" marker-end="url(#a2)"/>
  <text x="260" y="216" text-anchor="middle" fill="#1f2430" font-size="12">路径 A：VNC / QMP</text>
  <text x="260" y="244" text-anchor="middle" fill="#6b7280" font-size="11">只有像素和键鼠，通用</text>
</svg>
```

### 路径 A：从外面走 VNC，一套代码控制所有客机

VNC（底层是 RFB 协议）是一个很老、但非常通用的远程桌面协议：服务端不断把屏幕画面发过来，客户端把键鼠事件发回去。QEMU 可以直接在虚拟显卡上开 VNC 服务，不需要客机配合。macOS 客机可以打开系统自带的“屏幕共享”充当 VNC 服务，因为 Virtualization.framework 没有公开的 VNC 接口。容器和 Firecracker 方案则在客机里跑 Xvfb + x11vnc。

好处是协议统一，人也能在浏览器里用 noVNC 随时观看或接管。缺点是只有像素，拿不到界面结构，也执行不了命令。

```py title="示意：用 vncdotool 完成“截图 → 点击 → 按键”"
from vncdotool import api

client = api.connect("127.0.0.1::5923", password=None)

client.captureScreen("step-01.png")   # 看：截图交给模型
client.mouseMove(640, 400)            # 动：坐标来自模型的输出
client.mousePress(1)                  # 左键单击
client.keyPress("enter")
client.captureScreen("step-02.png")   # 动作后再截一张，给下一轮用

client.disconnect()
```

### 路径 B：客机内守护进程，补上 shell、文件和界面结构

在镜像里预装一个小服务，开机自启，通过 vsock 或 SSH 和宿主机通信。它负责路径 A 做不到的事：执行命令、读写文件、读取可访问性树，以及比 VNC 更快、更清晰的截图。两种系统各自对应的技术如下表，这张表就是让 AI 写守护进程时的需求清单。

| 能力 | Linux 客机 | macOS 客机 |
| --- | --- | --- |
| 截图 | X11 截图（或 Wayland 下的 portal） | ScreenCaptureKit / `screencapture` |
| 键鼠注入 | `xdotool`、uinput | CGEvent |
| 界面结构 | AT-SPI | Accessibility API（AXUIElement） |
| 命令与文件 | 普通进程和文件 API | 普通进程和文件 API |
| 需要的权限 | 基本没有 | “屏幕录制”和“辅助功能”（TCC 权限） |

> macOS 最容易卡住的地方是 TCC 权限：截图需要“屏幕录制”权限，注入键鼠和读取界面需要“辅助功能”权限，默认都要有人手动点“允许”。必须在制作黄金镜像时就预先授权（常见做法是关闭 SIP 后写入 TCC 数据库，或者用 MDM 描述文件）。现成的 macOS 沙箱镜像都在这一步上花了不少功夫，可以直接参考。

### 容易忽略的四个设计点

- **分辨率和坐标换算**：模型看到的截图常常被缩放过，点击前要把坐标换算回客机的真实分辨率；macOS 的 Retina 屏幕还要分清“点”和“像素”。最省事的做法是把客机固定在 1280×800 这类模型擅长的分辨率，不做缩放。
- **等界面稳定再截图**：动作刚做完就截图，经常会截到动画中途的画面。可以等到连续两帧画面不变，或者超时，再返回截图。
- **动作自带截图**：每个动作都直接返回动作之后的截图，省掉一次往返。
- **清掉屏幕干扰**：在镜像里关掉通知、屏保、自动更新和首次启动向导，否则 agent 会被突然弹出的窗口卡住。

## 网络控制：默认全部拒绝，规则只在宿主机上执行

第一原则：**客机不可信**。agent 在客机里通常有管理员权限，可以改代理设置、改防火墙、改 hosts 文件。所以任何在客机里做的限制都只是“建议”，真正的规则必须放在客机碰不到的地方，也就是宿主机。

推荐的结构见图 3：每台 VM 有自己独立的网络，宿主机防火墙默认丢弃它的所有流量，只放行两个出口：出站代理和受控 DNS。代理再按白名单决定哪些请求能出去，并把每个请求都记下来。

```svg title="图 3：网络闸门——客机只能通过两个出口访问外部" tone="light"
<svg viewBox="0 0 720 265" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif" font-size="13">
  <defs><marker id="a3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5b6475"/></marker></defs>
  <rect x="0" y="0" width="720" height="265" fill="#ffffff"/>
  <rect x="20" y="40" width="130" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="85" y="70" text-anchor="middle" fill="#1f2430">客机 A</text>
  <rect x="20" y="125" width="130" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="85" y="155" text-anchor="middle" fill="#1f2430">客机 B</text>
  <rect x="190" y="22" width="26" height="160" rx="4" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="203" y="15" text-anchor="middle" fill="#1f2430" font-size="12">宿主机防火墙</text>
  <rect x="260" y="35" width="210" height="62" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="365" y="61" text-anchor="middle" fill="#1f2430" font-weight="600">出站代理</text>
  <text x="365" y="83" text-anchor="middle" fill="#1f2430" font-size="12">白名单 · 审计日志 · 凭证注入</text>
  <rect x="260" y="125" width="210" height="46" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
  <text x="365" y="153" text-anchor="middle" fill="#1f2430" font-weight="600">受控 DNS</text>
  <rect x="540" y="35" width="160" height="62" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
  <text x="620" y="61" text-anchor="middle" fill="#1f2430">互联网</text>
  <text x="620" y="83" text-anchor="middle" fill="#6b7280" font-size="12">仅白名单内的域名</text>
  <line x1="150" y1="62" x2="258" y2="62" stroke="#5b6475" marker-end="url(#a3)"/>
  <line x1="150" y1="145" x2="258" y2="80" stroke="#5b6475" marker-end="url(#a3)"/>
  <line x1="150" y1="158" x2="258" y2="150" stroke="#5b6475" marker-end="url(#a3)"/>
  <line x1="470" y1="66" x2="538" y2="66" stroke="#5b6475" marker-end="url(#a3)"/>
  <rect x="20" y="200" width="680" height="48" rx="8" fill="#fbf4f4" stroke="#d9b8b8"/>
  <text x="360" y="229" text-anchor="middle" fill="#7a2a2a" font-size="12">全部丢弃：直连互联网 · 访问宿主机其他端口 · 内网与云元数据地址 · 客机之间互访</text>
</svg>
```

### 三层控制各管什么？

网络控制分三层，每层解决不同的问题，缺了哪一层都会留下绕过的路。

| 层 | 做什么 | 常用工具 |
| --- | --- | --- |
| 网络隔离（L2/L3） | 每台 VM 独立网段；禁止客机互访；禁止访问宿主机、内网和云元数据地址 `169.254.169.254`（这是泄露云凭证的经典路径） | Linux：TAP + 网桥或网络命名空间，加 nftables；macOS：vmnet + pf，或用户态网络栈 |
| 出站代理（L7） | 按域名白名单放行 HTTP(S)；记录每个请求；可以替 agent 带上 API 密钥 | mitmproxy、Squid、Envoy |
| DNS | 只解析白名单内的域名，防止有人借 DNS 查询偷偷把数据带出去 | dnsmasq、CoreDNS |

在 Linux 宿主机上，第一层落地成防火墙规则大概是右边这样。核心只有两句：从 VM 网卡进来、要转发出去的流量全部丢弃；发给宿主机本身的流量，只放行代理和 DNS 两个端口。

```text title="示意：/etc/nftables.d/vm-sandbox.nft"
table inet vm_sandbox {
  chain input {
    type filter hook input priority 0; policy accept;
    iifname "tap-*" tcp dport 3128 accept   # 只允许连宿主机上的代理
    iifname "tap-*" udp dport 53 accept     # 和受控 DNS
    iifname "tap-*" drop                    # 访问宿主机的其他流量一律丢弃
  }
  chain forward {
    type filter hook forward priority 0; policy accept;
    iifname "tap-*" drop                    # 客机不能直连互联网，也不能互访
  }
}
```

### 代理负责白名单和审计，还能替客机保管密钥

代理有两种工作方式。**不解密 TLS** 时，只能看到要连接的域名（来自 CONNECT 请求或 TLS 的 SNI 字段），用来做域名白名单已经够了。**解密 TLS** 需要在镜像里预装代理的 CA 证书，之后能看到完整的请求，可以做 URL 级别的规则和完整审计；代价是部分做了证书固定（certificate pinning）的应用会连不上。建议先只按域名过滤，确实需要时再开启解密。

> **凭证注入**：真实的 API 密钥只放在代理里，客机里只有一个占位符，代理转发请求时再替换成真值。这样即使 agent 被网页里的提示注入攻破，也拿不到真正的密钥。

```py title="示意：mitmproxy 插件 allowlist.py"
from mitmproxy import http

ALLOW = {"github.com", "pypi.org", "files.pythonhosted.org"}

def allowed(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in ALLOW)

def http_connect(flow: http.HTTPFlow):
    # HTTPS 隧道建立之前：只看得到域名
    if not allowed(flow.request.host):
        flow.response = http.Response.make(403, b"blocked by sandbox policy")

def request(flow: http.HTTPFlow):
    # 明文 HTTP，或者已经解密的 HTTPS
    if not allowed(flow.request.pretty_host):
        flow.response = http.Response.make(403, b"blocked by sandbox policy")
```

### 把网络策略做成按任务下发的配置

不同任务需要的网络不一样：跑测试可能完全不需要联网，装依赖只需要几个包仓库，做调研才需要开放访问。把策略写成配置，在创建会话时随请求下发；编排器把它翻译成具体的防火墙规则和代理配置。

```yaml title="示意：创建会话时附带的网络策略"
network:
  mode: allowlist          # offline | allowlist | open（开放但完整审计）
  allow:
    - github.com
    - "*.pythonhosted.org"
    - pypi.org
  block_private_ranges: true   # 内网、宿主机、云元数据地址
  tls_inspect: false           # 只看域名，不解密
  inject_credentials:
    - host: api.github.com
      header: Authorization
      secret_ref: vault://agent/github-token
  log: full
```

### Mac 宿主机上的网络能控制到什么程度？

Virtualization.framework 给网卡提供了三种接法，选哪种决定了你能把网络管到多细：

- **NAT**（`VZNATNetworkDeviceAttachment`）：最简单，客机流量经宿主机上的虚拟网桥出去。可以在这个网桥接口上用 pf 写规则，但调试麻烦，多台 VM 共用一个网段，也不好按单台 VM 区分。
- **桥接**（`VZBridgedNetworkDeviceAttachment`）：客机直接接入物理网络，需要 Apple 授予的特殊 entitlement，而且和沙箱的目标相反，不推荐。
- **文件句柄**（`VZFileHandleNetworkDeviceAttachment`）：宿主机程序直接拿到客机的原始以太网帧，可以接上 gvisor-tap-vsock 这类用户态网络栈，由你的代码决定每个包去哪。Lima 和 Podman 在 Mac 上就用这个思路，它的控制力最强。

建议原型阶段用 NAT 加 pf，只允许客机连到宿主机上的代理；做成产品时换成文件句柄加用户态网络栈，这样每台 VM 的网络都完全由你的程序掌控。

## 生命周期：每个任务一台新机器，用完即弃

不要复用跑过任务的机器。上一个任务留下的文件、登录状态和被改过的配置都会污染下一个任务，也会让结果无法复现。正确的流程是图 4 这样一条单向流水线。

```svg title="图 4：从镜像到销毁的单向流水线" tone="light"
<svg viewBox="0 0 720 150" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif" font-size="13">
  <defs><marker id="a4" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5b6475"/></marker></defs>
  <rect x="0" y="0" width="720" height="150" fill="#ffffff"/>
  <g>
    <rect x="15" y="30" width="100" height="50" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
    <text x="65" y="60" text-anchor="middle" fill="#1f2430">黄金镜像</text>
    <text x="65" y="105" text-anchor="middle" fill="#6b7280" font-size="11">脚本构建</text>
    <text x="65" y="122" text-anchor="middle" fill="#6b7280" font-size="11">带版本号</text>
    <rect x="133" y="30" width="100" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
    <text x="183" y="60" text-anchor="middle" fill="#1f2430">写时复制克隆</text>
    <text x="183" y="105" text-anchor="middle" fill="#6b7280" font-size="11">APFS clone</text>
    <text x="183" y="122" text-anchor="middle" fill="#6b7280" font-size="11">qcow2 差异盘</text>
    <rect x="251" y="30" width="100" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
    <text x="301" y="60" text-anchor="middle" fill="#1f2430">预热池</text>
    <text x="301" y="105" text-anchor="middle" fill="#6b7280" font-size="11">提前开机待命</text>
    <text x="301" y="122" text-anchor="middle" fill="#6b7280" font-size="11">或从内存快照恢复</text>
    <rect x="369" y="30" width="100" height="50" rx="8" fill="#eaf0fd" stroke="#2457d6"/>
    <text x="419" y="60" text-anchor="middle" fill="#1f2430">执行任务</text>
    <text x="419" y="105" text-anchor="middle" fill="#6b7280" font-size="11">策略先生效</text>
    <text x="419" y="122" text-anchor="middle" fill="#6b7280" font-size="11">全程录像</text>
    <rect x="487" y="30" width="100" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
    <text x="537" y="60" text-anchor="middle" fill="#1f2430">收集产物</text>
    <text x="537" y="105" text-anchor="middle" fill="#6b7280" font-size="11">文件 · 录屏</text>
    <text x="537" y="122" text-anchor="middle" fill="#6b7280" font-size="11">操作与网络日志</text>
    <rect x="605" y="30" width="100" height="50" rx="8" fill="#f5f7fb" stroke="#c3cad8"/>
    <text x="655" y="60" text-anchor="middle" fill="#1f2430">销毁</text>
    <text x="655" y="105" text-anchor="middle" fill="#6b7280" font-size="11">删除差异盘</text>
    <text x="655" y="122" text-anchor="middle" fill="#6b7280" font-size="11">释放名额</text>
  </g>
  <line x1="115" y1="55" x2="131" y2="55" stroke="#5b6475" marker-end="url(#a4)"/>
  <line x1="233" y1="55" x2="249" y2="55" stroke="#5b6475" marker-end="url(#a4)"/>
  <line x1="351" y1="55" x2="367" y2="55" stroke="#5b6475" marker-end="url(#a4)"/>
  <line x1="469" y1="55" x2="485" y2="55" stroke="#5b6475" marker-end="url(#a4)"/>
  <line x1="587" y1="55" x2="603" y2="55" stroke="#5b6475" marker-end="url(#a4)"/>
</svg>
```

### 黄金镜像：把所有一次性的准备工作提前做完

镜像里应该包含：操作系统、自动登录、固定分辨率、关闭更新、通知和屏保、预装常用软件、客机内守护进程、（macOS）预先授好的 TCC 权限，以及（需要解密 TLS 时）代理的 CA 证书。

镜像要用脚本构建，不要手工点出来。这样每个版本都能追溯、能重建，出问题时也能回滚。macOS 可以用 Packer 加 Tart 插件，Linux 可以用 Packer 或 cloud-init。右边是一个 Packer 配置的大致样子，构建步骤就是一组 shell 脚本，很适合交给 AI 来写。

```text title="示意：images/agent-macos.pkr.hcl"
source "tart-cli" "agent-macos" {
  vm_base_name = "ghcr.io/cirruslabs/macos-sequoia-base:latest"
  vm_name      = "agent-macos-v1"
  cpu_count    = 4
  memory_gb    = 8
  ssh_username = "admin"
  ssh_password = "admin"
}

build {
  sources = ["source.tart-cli.agent-macos"]
  provisioner "shell" {
    scripts = [
      "scripts/disable-updates-and-notifications.sh",
      "scripts/set-resolution-and-autologin.sh",
      "scripts/install-agentd.sh",
      "scripts/grant-tcc-permissions.sh",
    ]
  }
}
```

### 克隆与快照：把拿到机器的时间从分钟级压到秒级

写时复制克隆几乎是瞬间完成的：macOS 上用 APFS 的 clonefile（Tart 克隆虚拟机就靠它），Linux 上用 qcow2 的 backing file。克隆本身很快，慢的是开机：macOS 冷启动到登录完成要几十秒。

所以还要两个加速手段。一是**预热池**：提前开好几台机器等着。二是**内存快照**：保存“已经登录、桌面就绪”时的完整运行状态，下次直接恢复。Virtualization.framework 从 macOS 14 起支持保存和恢复虚拟机运行状态，QEMU 也支持，恢复通常只要几秒。

```bash title="示意：两种平台上的写时复制克隆"
# macOS：cp -c 走 APFS clonefile，几 GB 的磁盘瞬间复制，只占改动的空间
cp -c ~/vms/agent-macos-v1/disk.img ~/vms/task-123/disk.img

# Linux：基于黄金镜像创建差异盘，黄金镜像本身保持只读
qemu-img create -f qcow2 -b /images/ubuntu-golden-v3.qcow2 -F qcow2 /vms/task-123.qcow2
```

### 容量与调度：macOS 名额是最稀缺的资源

每台 Mac 同时只能跑两台 macOS 虚拟机，所以编排器必须清楚每台宿主机还剩几个名额，并且能让任务排队。Linux 客机主要受内存限制。不管哪种客机，都要有任务超时、心跳检测（发现卡死的 VM）和强制回收，否则一台卡住的机器会永远占着名额。

## 给 agent 的接口：把一台机器包装成一组工具

agent 不需要知道 VNC、vsock 这些细节，它只看到一组工具。GUI 动作可以参考 Anthropic computer use 工具的设计（截图、点击、输入、按键、滚动等），再加上 shell 和文件工具。

对外先暴露一个 HTTP API，再包一层 MCP server，这样任何支持 MCP 的 agent 都能直接接入。动作的设计要和模型的使用习惯一致：坐标基于截图，按键用 `"cmd+s"` 这样的组合写法，并且每个动作都直接返回新截图。

```text title="示意：对 agent 暴露的工具集"
# 会话
createSession({ os: "macos" | "linux", image, network: NetworkPolicy }) → { sessionId }
closeSession(sessionId) → { artifacts, recordingUrl, networkLog }

# GUI（每个动作都返回动作之后的截图）
screenshot() → png
click(x, y, button?) · doubleClick(x, y) · drag(from, to)
type(text) · key("cmd+s") · scroll(x, y, dx, dy)
accessibilityTree() → 界面结构（可选，来自客机内守护进程）

# 非 GUI
shell(cmd, timeoutSec) → { stdout, stderr, exitCode }
readFile(path) · writeFile(path, bytes)
```

### 可观测性：让每一步都能回放，让人能随时接管

调试 agent 时最需要的是“它当时看到了什么、做了什么”。所以从一开始就要记录三类数据：**录屏**（把 VNC 画面录下来）、**结构化动作日志**（每一步的截图、模型输出、执行的动作和耗时）、**网络日志**（代理记录的每个请求和放行结果）。

再加一个**人工接管**入口：在浏览器里用 noVNC 实时观看，遇到登录、验证码这类 agent 处理不了的步骤，由人操作完再交还给 agent。

### 上线前过一遍这份安全清单

- 客机里不放任何长期凭证；需要的密钥通过代理注入，或者使用短期令牌。
- 宿主机上的控制服务和 VNC 只监听本机或内网，并设置认证，不暴露到公网。
- 给每台 VM 设资源上限：CPU、内存、磁盘配额和任务最长时间。
- 剪贴板共享和共享目录默认关闭，按任务需要再开启。
- 及时更新宿主机系统和 hypervisor。虚拟机逃逸漏洞很少见，但确实存在。

## 学习路线：五个阶段，每个阶段交付一个能跑的东西

既然开发交给 AI，你学习的重点就不是记 API，而是三件事：**能说清楚要什么**、**能判断 AI 的方案是否合理**、**能验收结果**。每个阶段先用现成工具亲手体验一遍，建立直觉，再让 AI 写代码。

| 阶段 | 时间 | 学什么 | 交付物（验收标准） |
| --- | --- | --- | --- |
| 1. 体验 | 3–5 天 | 本文第 02 节的概念；用 UTM 或 Tart 安装 macOS 和 Ubuntu 桌面；用 VNC 客户端连进去 | 能手动启停两种客机，并通过 VNC 远程操作它们 |
| 2. GUI 控制 | 1 周 | VNC 协议、xdotool、截图坐标换算；读 Anthropic 的 computer-use-demo | 一个脚本循环执行“截图 → 调模型 → 点击/输入”，在 Linux 容器里完成“打开浏览器搜索某个词” |
| 3. 网络控制 | 1 周 | NAT 与网桥、nftables 或 pf、HTTP 代理、TLS 与 CA 证书 | 客机只能访问白名单域名，每个请求都有日志；在客机里改代理设置或直连 IP 都绕不过去 |
| 4. 生命周期 | 1–2 周 | 镜像构建、写时复制、内存快照；读 Tart 的源码 | 一条命令从黄金镜像拉起一台干净 VM，任务结束自动销毁；有预热池时几秒内就能拿到机器 |
| 5. 平台化 | 2–4 周 | API 设计、MCP、调度与排队、录像 | 统一的 API 和 MCP server，同时管理 macOS 和 Linux 客机，带录屏和网络日志 |

### 用 AI 开发时，一个阶段交一份任务说明

直接让 AI “做一个 agent 虚拟机平台”，得到的多半是一堆没法验收的代码。更可靠的做法是每个阶段写一份右边这样的任务说明：背景、目标、约束和验收方式都写清楚，并要求 AI 先给方案、等你确认后再写代码。

其中“约束”最重要。例如“假设客机内的 agent 有 root 权限”这一句，直接决定了 AI 会把规则放在宿主机上，而不是图省事去改客机的代理设置。“验收”要求它写出绕过测试，这样你不懂 nftables 也能判断规则是否有效。

```prompt title="示意：阶段 3 交给 AI 的任务说明"
# 任务：阶段 3 —— 为 Linux 客机实现出站网络白名单

## 背景
宿主机是 Ubuntu 24.04，客机由 QEMU/KVM 启动，每台 VM 有一块专属的 TAP 网卡。

## 目标
- 客机只能访问 policy.yaml 里列出的域名，其余全部拒绝
- 每个请求写一行 JSON 日志：时间、VM、域名、是否放行

## 约束
- 规则必须在宿主机上执行；假设客机内的 agent 有 root 权限
- 不解密 TLS，按 CONNECT / SNI 里的域名判断

## 验收
- 写一个测试脚本：在客机内 curl 白名单内外各一个域名，并尝试修改代理设置、直连 IP 来绕过
- 先给我方案和可能的风险，等我确认后再写代码
```

### 哪些决定必须由你来做？

AI 很擅长写胶水代码、镜像构建脚本、防火墙规则和日志监控，但下面这些决定涉及成本、安全和产品取舍，必须由你拍板，再写进任务说明的“约束”里：

- **宿主平台和预算**：Mac 的数量直接决定了能同时跑多少台 macOS 客机。
- **隔离强度**：Linux 客机用容器还是虚拟机，取决于你对任务的信任程度。
- **网络默认策略**：默认断网、白名单还是开放审计；要不要解密 TLS。
- **数据保留**：录屏和日志里可能有敏感信息，保留多久、谁能看。
- **许可证**：商用前核对每个依赖的许可证。例如 Tart 不是传统意义上的开源许可，超过一定规模需要商业授权。

### 值得读的开源项目

下面这些项目各自解决了本文的一部分问题，按学习阶段对照着读。不需要读懂全部代码，重点看表里列出的部分。

| 项目 | 看什么 | 对应阶段 |
| --- | --- | --- |
| Tart（cirruslabs/tart） | Swift 代码量小，最适合学 Virtualization.framework：虚拟机配置、APFS 克隆、用 OCI 仓库分发镜像 | 1、4 |
| Cua / Lume（trycua/cua） | 最接近本文目标：面向 agent 的 macOS/Linux 沙箱和 computer-use 接口 | 2、5 |
| UTM | 图形化工具，同时封装了 QEMU 和 Virtualization.framework，适合体验阶段 | 1 |
| Anthropic computer-use-demo（anthropics/claude-quickstarts） | Docker + Xvfb + VNC 的最小 computer-use 环境 | 2 |
| OSWorld | agent 评测基准：如何管理虚拟机、重置环境、判断任务是否完成 | 4、5 |
| E2B Desktop、Firecracker | 大规模 Linux 沙箱的做法 | 5 |
| Lima、gvisor-tap-vsock | 在 Mac 上管理 Linux 虚拟机，以及用户态网络栈的成熟实现 | 3 |
| mitmproxy | 出站代理、白名单和审计 | 3 |

> 一个务实的起点：先用 Tart 或 Lume 当后端，把精力放在网络闸门、镜像流水线和 agent API 这三块上。等这些稳定了，再决定要不要直接调用 Virtualization.framework，把它们替换掉。

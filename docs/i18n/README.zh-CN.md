<p align="center">
  <img src="../assets/logo.svg" width="80" height="80" alt="Render Node 标志">
</p>

<h1 align="center">Render Node</h1>

<p align="center">
  <strong>在浏览器中管理 Blender 渲染。</strong><br>
  单个节点，持久化任务队列，支持单 GPU 和多 GPU。
</p>

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.ru.md">Русский</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/NikkZhur/Render-Node/actions/workflows/release.yml"><img src="https://github.com/NikkZhur/Render-Node/actions/workflows/release.yml/badge.svg?branch=master" alt="检查与发布工作流状态"></a>
  <img src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" alt="Python 3.13">
  <img src="https://img.shields.io/badge/React-19-149ECA?logo=react&logoColor=white" alt="React 19">
  <img src="https://img.shields.io/badge/Blender-CUDA%20%2F%20OptiX-E87D0D?logo=blender&logoColor=white" alt="Blender，支持 CUDA 和 OptiX">
</p>

<p align="center">
  <a href="#功能">功能</a> · <a href="#快速开始">快速开始</a> · <a href="#部署">部署</a> · <a href="#文档">文档</a>
</p>

![Render Node 界面：场景设置、实时预览、各 GPU 的渲染进度、任务和硬件指标](../assets/dashboard.png)

<p align="center"><sub>使用演示数据的界面预览。支持桌面和移动端布局，以及浅色和深色主题。</sub></p>

Render Node 将 Linux 主机或 GPU Pod 变成个人 Blender 渲染工作台。
上传场景，选择 Blender 版本和计算资源，将任务加入队列，随后在同一页面查看进度并下载结果。

本项目面向**单个操作者及其自己的可信场景**。任务按先进先出（FIFO）顺序执行，
同一时间只有一个逻辑任务处于渲染状态，该任务可以使用多块 GPU。

## 功能

| | 可以做什么 |
|---|---|
| **从场景到任务** | 上传 `.blend` 或 ZIP 文件，选择渲染引擎和帧范围，在首次启动前编辑任务或替换场景。 |
| **队列管理** | 启动、取消、重试，或创建可编辑的副本以重新渲染。任务历史保存在 SQLite 中。 |
| **GPU 渲染** | 使用 CUDA 或 OptiX，选择 GPU，在按帧分配和共享渲染进程之间切换。也支持 CPU 渲染。 |
| **实时反馈** | 通过 WebSocket 查看总体及各 worker 的进度、已用时间、预计剩余时间、预览和 Blender 日志。 |
| **渲染结果** | 浏览帧，下载原图、帧 ZIP 压缩包和日志。后端重启后，任务历史和产物元数据仍会保留。 |
| **版本与访问** | 下载、安装并手动激活 Blender 版本。通过一次性邀请链接授权其他浏览器。 |

### 两种 GPU 模式

| 模式 | 工作方式 | 适用场景 |
|---|---|---|
| **Frame per GPU** | 每块选中的 GPU 运行独立的 Blender 进程，渲染分配给它的帧，并遵循场景的 `frame_step`。 | 并行渲染动画帧。 |
| **All GPUs per frame** | 一个 Blender 进程使用所有选中的 GPU 渲染当前帧。 | 多块 GPU 共同渲染同一帧。 |

使用 CUDA/OptiX 且选择至少两块 GPU 时，默认采用 **Frame per GPU**。
CPU 和单 GPU 任务使用共享进程模式。多 GPU 渲染在队列中仍然是一个任务。

## 快速开始

在 Linux 上进行本地开发需要 **Python 3.13**、**uv**、**Node.js 22.12+** 和 **make**。
启动界面不需要预先安装 Blender。

```bash
git clone https://github.com/NikkZhur/Render-Node.git
cd Render-Node

(cd backend && uv sync --frozen)
npm --prefix frontend ci

export RENDER_NODE_WORKSPACE="$HOME/render-node-data"
export RENDER_NODE_DATABASE_URL="sqlite+aiosqlite:///$RENDER_NODE_WORKSPACE/database/render-node.sqlite3"

make dev
```

打开 **[localhost:5173](http://localhost:5173)**。`make dev` 会执行数据库迁移，
在 `8000` 端口启动 API，等待就绪后在 `5173` 端口启动 Vite。
按 `Ctrl+C` 可停止两个服务。数据保存在上面指定的工作目录中；
打开新的终端后，请重新设置这两个环境变量。

1. 打开 **Blender versions**，下载、安装并激活一个版本。
2. 创建任务，上传 `.blend` 场景或包含场景及其资源的 ZIP 文件。
3. 选择渲染引擎、帧和计算设备，然后启动渲染。
4. 查看预览和日志，在 **Artifacts** 中下载已完成的帧。

节点尚未被认领时，第一个打开界面的浏览器会获得访问权限。
要授权其他浏览器，请使用 **Pair another device**。后端重启会清除浏览器会话，
但保留持久化的任务和文件。开发服务器依赖本地环境的资源限制，仅用于可信的开发环境。

如需只预览界面，可运行 `npm --prefix frontend run dev:mock` 并打开同一端口。
该模式使用演示数据，不会渲染场景。

关于分别启动服务、配置、单元检查和 Playwright 场景，请参阅[开发指南](../development.md)。

## 部署

原生安装程序面向 **Ubuntu 22.04/24.04 x86_64 GPU Pod**，要求 `nvidia-smi` 正常工作。
以 root 身份运行，并通过服务商的 HTTPS 代理开放 **8080** 端口：

```bash
curl -fsSL https://raw.githubusercontent.com/NikkZhur/Render-Node/master/install.sh | bash
```

> [!NOTE]
> 安装程序需要已发布的稳定版 GitHub Release 安装包。目前尚未发布稳定版，
> 请先按上文从源码启动。参阅[发布页面](https://github.com/NikkZhur/Render-Node/releases)
> 和[发布指南](../../deploy/README.md#publication)。

安装程序会先准备好新版本再切换；如果就绪检查失败，会回滚到原版本。
仓库也提供 Docker 镜像构建配置，供直接运行镜像的平台使用。
两种部署方式在生产环境中渲染都需要已委派且可写的 **cgroup v2** 子树；
HTTP 服务就绪并不代表渲染器可用。

[安装、更新、回滚、浏览器配对与备份 →](../../deploy/README.md)

## 项目结构

**React + Vite** · **TanStack Query + Zustand** · **FastAPI** ·
**SQLite + SQLAlchemy + Alembic** · **Blender 子进程 + NVIDIA NVML**

```text
frontend/   React 界面、API 客户端和浏览器检查
backend/    FastAPI、任务队列、Blender 执行、数据存储和测试
deploy/     运行脚本和部署指南
scripts/    开发启动器、节点管理和发布打包
docs/       架构、开发指南、项目状态和翻译
```

单个节点、一个 API 进程、SQLite 存储，无需额外的队列或数据库服务。
模块边界及 REST/WebSocket 契约见[架构文档](../architecture.md)。

## 文档

| 指南 | 内容 | 语言 |
|---|---|---|
| [开发](../development.md) | 环境设置、启动、检查和浏览器测试 | 英语 |
| [部署](../../deploy/README.md) | GPU Pod 安装、Docker、更新、回滚和备份 | 英语 |
| [后端](../../backend/README.md) | API、Blender 版本管理、渲染和安全 | 英语 |
| [架构](../architecture.md) | 模块职责与系统契约 | 俄语 |
| [实现状态](../implementation-state.md) | 已验证的行为、已知问题和待完成的检查 | 俄语 |
| [配置](../../.env.example) | 环境变量与资源限制 | 英语 |

已在一块 NVIDIA GPU 上完成真实 CUDA/OptiX 渲染的冒烟检查。
多块物理 GPU、生产环境安装与回滚，以及通过委派的内核 cgroup v2 实现的隔离，
仍需在目标环境中验证。使用 fake Blender 的测试只能验证应用行为，不能代替上述检查。
当前验证结果和已知限制记录在[实现状态](../implementation-state.md)中。

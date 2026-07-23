# Terminal Label

[![Test](https://github.com/Valdemar-Yu/terminal_label/actions/workflows/test.yml/badge.svg)](https://github.com/Valdemar-Yu/terminal_label/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

让多个 Claude Code 标签页能直接区分模型和任务：

```text
GPT-5.6 Sol · terminal-label README
Opus 4.8 · auth refactor
Sonnet 5 · release checks
```

Terminal Label 会把终端标签页标题同步为当前模型和 Claude Code session
名称。执行 `/model` 或 `/rename` 后会继续更新，也能保留原有自定义 status
line，不发送遥测数据。

[English](README.md)

## 环境要求

- Claude Code 2.1.217 或更高版本
- Python 3.9 或更高版本
- macOS 或 Linux，包括 WSL

## 安装

把仓库添加为 Claude Code marketplace，再安装插件：

```bash
claude plugin marketplace add Valdemar-Yu/terminal_label
claude plugin install terminal-label@terminal-label
```

启动或重载 Claude Code，然后运行：

```text
/terminal-label:setup
```

setup 会对当前 Claude 配置目录做可撤销修改。执行后新开一个 Claude Code
session，让 Claude Code 原生动态标题不再覆盖 Terminal Label。

启动时可以直接给 session 命名：

```bash
claude --name "terminal-label README"
```

也可以随时重命名当前 session：

```text
/rename terminal-label README
```

session 尚无自定义名称或自动生成名称时，Terminal Label 使用当前目录名。

### 一次安装到整个 claude-all

如果通过 [claude-all](https://github.com/Valdemar-Yu/claude-all) 启动多个
provider，先在当前 profile 安装插件，然后运行：

```text
/terminal-label:setup-claude-all
```

从源码 checkout 运行的等价命令是：

```bash
./plugins/terminal-label/bin/terminal-label install-claude-all
```

它会发现 `~/.claude-all/profiles/*.env`，对所有不重复的 Claude 配置目录安装
Terminal Label，保留已有统一 status line，并适配 PLBBL、Fugu 等 claudish
profile。程序不会 source profile 文件，也不会把 token 值写入状态文件。

只检查全部 profile：

```bash
./plugins/terminal-label/bin/terminal-label doctor-claude-all
```

撤销批量安装：

```bash
./plugins/terminal-label/bin/terminal-label uninstall-claude-all
```

只有 profile 当前 SHA-256 与安装记录一致时才会自动恢复。如果安装后又手工修改过
profile，卸载会报告冲突而不是覆盖。通过 `claude-all add` 新增 profile 后，再运行
一次 `install-claude-all`。

### 多套 Claude 配置

插件遵循 `CLAUDE_CONFIG_DIR`。每套 Claude Code 配置需要分别安装和 setup：

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" \
  claude plugin marketplace add Valdemar-Yu/terminal_label
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" \
  claude plugin install terminal-label@terminal-label
CLAUDE_CONFIG_DIR="$HOME/.claude-plbbl" claude
```

进入该 session 后运行 `/terminal-label:setup`。Terminal Label 不会自动扫描或
改写其他配置目录。

### 从源码试用

```bash
git clone https://github.com/Valdemar-Yu/terminal_label.git
cd terminal_label
claude --plugin-dir ./plugins/terminal-label
```

在这个开发 session 中运行 `/terminal-label:setup`。

### macOS Terminal 只显示标签内容

Terminal.app 默认会在自定义标题后拼接工作目录、活动进程、完整参数、TTY 和尺寸。
运行下面的 skill，可让存在 Terminal Label 标题的标签只显示 `模型 · session`：

```text
/terminal-label:configure-terminal-app
```

从源码 checkout 或稳定运行时执行的等价命令：

```bash
./plugins/terminal-label/bin/terminal-label configure-terminal-app
```

Terminal.app 会在进程生命周期内缓存 profile 标题组件。配置后必须退出并重新打开
Terminal.app；只在同一进程中新建窗口不会生效。检查和恢复命令：

```bash
./plugins/terminal-label/bin/terminal-label doctor-terminal-app
./plugins/terminal-label/bin/terminal-label restore-terminal-app
```

Terminal Label 会关闭该 profile 的窗口/标签标题组件，包括 cwd、进程名和参数、
TTY、settings 名称及尺寸。所有使用该 Terminal profile 的标签都会受影响；如果普通
shell 标签仍需这些组件，应使用单独 profile。完整 Terminal plist 会以 `0600` 权限
备份在本机；如果之后手工改变过这些设置，恢复命令会报告冲突而不是覆盖。

## 工作原理

Claude Code 会把 `model.display_name`、`session_name` 和当前工作区等实时信息
传给 status line 进程。setup 安装一个不依赖第三方包的 Python 代理：

1. 生成并清理 `模型 · session`；
2. 即使 hook/status line 子进程没有控制终端，也能从 Claude 主进程解析真实 TTY；
3. 同时写入 OSC 1、2、0，覆盖标签页、窗口和组合标题；
4. 把原始 JSON 继续交给原有 status line 命令，并原样返回其输出。

原 status line 刷新慢于 5 秒时，代理会改成每 5 秒刷新一次，避免空闲状态下执行
`/model` 或 `/rename` 后标签长期不更新。setup 还会为后续 session 禁用 Claude
Code 内置动态标题。原 status line 和标题
设置只保存在本地，供卸载时恢复。运行时不读取 prompt 和 transcript 内容。

## 命令

| 命令 | 用途 |
| --- | --- |
| `/terminal-label:setup` | 安装或更新单个 profile 的 status line 代理 |
| `/terminal-label:setup-claude-all` | 发现并安装全部 claude-all profile |
| `/terminal-label:configure-terminal-app` | 隐藏 Terminal.app 额外标题组件 |
| `/terminal-label:doctor` | 检查配置、运行时、终端和 tmux 检测结果 |
| `/terminal-label:uninstall` | 恢复安装前的 Claude Code 设置 |

已安装的运行时还提供 `render`、`doctor`、`install`、`uninstall`、
`install-claude-all`、`doctor-claude-all` 和 `uninstall-claude-all` 子命令，供
本地开发和脚本调用。

## 终端兼容性

| 环境 | 状态 | 说明 |
| --- | --- | --- |
| macOS Terminal.app | 支持 | 已实测 OSC 0/2 修改选中标签标题 |
| iTerm2 | 支持 | 使用 OSC 1/2/0 |
| Ghostty | 支持 | 使用 OSC 1/2/0 |
| WezTerm | 支持 | 使用 OSC 1/2/0 |
| kitty | 支持 | 使用 OSC 1/2/0 |
| tmux | 尽力支持 | 更新活动 pane/window；建议每个 window 只运行一个 Claude session |
| WSL + Windows Terminal | 尽力支持 | 需要可写的 `/dev/tty` |
| 原生 Windows | 暂不支持 | 没有 `/dev/tty` 输出路径 |

部分终端 profile 会忽略应用设置的标题。如果 setup 成功但标签文字不变，请在
终端 profile 中允许应用修改标题。

## 卸载

运行：

```text
/terminal-label:uninstall
```

卸载会恢复 setup 保存的完整 status line，并恢复之前的
`CLAUDE_CODE_DISABLE_TERMINAL_TITLE`。如果其他工具或后续手工操作已经替换了
Terminal Label 的命令，卸载会拒绝覆盖这项新配置。如果已经先删了插件，仍可用
稳定运行时自行卸载：

```bash
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/terminal-label/bin/terminal-label" uninstall
```

之后可以移除插件和 marketplace：

```bash
claude plugin uninstall terminal-label@terminal-label
claude plugin marketplace remove terminal-label
```

## 隐私与安全

- 运行时不发送网络请求和遥测；批量安装只通过 Claude plugin CLI 拉取公开的
  GitHub marketplace。
- 不解析 prompt 或 transcript。
- 模型名和 session 名写入终端转义序列前会删除控制字符。
- 本地状态可能包含原 status line 命令，文件权限仅允许当前用户读取。批量状态只
  记录路径和 SHA-256，不记录 token 值；相邻 profile 备份保留在本机并使用 `0600`。
- 原子写入配置，首次修改前创建备份。

漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 开发

```bash
python3 -m unittest discover -s tests -v
claude plugin validate --strict .
```

提交 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)

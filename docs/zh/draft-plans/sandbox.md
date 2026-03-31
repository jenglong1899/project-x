云端：e2b

codex cli的方案：
https://deepwiki.com/search/_e0cb8278-aa8f-42b9-a3bd-b4c93b89c72f?mode=fast
在macos用的是系统自带的seatbelt，
linux是bubblewrap
windows是使用 Restricted Tokens 和自定义的沙盒用户系统 - 创建 CodexSandboxOffline 和 CodexSandboxOnline 用户来实现隔离 setup_orchestrator.rs:37-38

nanoclaw：
https://deepwiki.com/search/_2f873331-9d1e-42c4-8413-8b479450ae5c?mode=fast
1. Docker（默认）
跨平台支持（macOS 和 Linux） README.md:73
提供容器级别的文件系统隔离 SECURITY.md:16-22
2. Docker Sandboxes（微虚拟机隔离）
提供超级监督器级别的隔离 docker-sandboxes.md:8-18
每个代理运行在独立的微虚拟机内的容器中
两层隔离：代理容器 + 虚拟机边界 docker-sandboxes.md:18
3. Apple Container（macOS 原生）
仅适用于 macOS SKILL.md:38
更轻量级的原生运行时 README.md:154
可通过 /convert-to-apple-container 技能切换 SKILL.md:11-17
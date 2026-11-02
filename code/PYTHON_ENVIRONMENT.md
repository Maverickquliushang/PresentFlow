# Python environment

本机为 PresentFlow 单独建立集中环境 `presentflow`，Python 3.13.7；同版本共用基础解释器，依赖与其他项目隔离。绑定配置位于不随源码分发的 `.python-environment.json`。

每个新的 PowerShell 进程先加载本目录或项目根目录的 `project-env.ps1`。配置中的 environmentRoot 指向用户集中环境目录；脚本只影响当前进程。临时文件和缓存位于本目录 `.tmp/`、`.cache/`，没有创建项目内虚拟环境，也没有改全局设置。

其他机器按自己的 Python 环境安装项目；不需要复制本机 JSON。运行 `presentflow doctor` 查看该命令实际运行机器的环境，不能把远程容器的 GPU/RAM 当作用户电脑配置。

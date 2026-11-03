# Windows 剪映草稿兼容组件

剪映 11.7.5.14616 的 `videoeditor.dll` 在独立读取进程中可能返回
`ok=True, size=0`，而草稿仍可在剪映中正常打开。仅回退到 Apps 下的旧版本
不可靠：剪映升级时会删除旧版本目录。

读取程序支持独立保存的兼容组件：

```
%LOCALAPPDATA%\LiveCut\jianying-runtime\<版本号>\videoeditor.dll
```

此目录还需要该 DLL 的依赖库，不能只复制 `videoeditor.dll`。
组件来自用户本机或官方安装包，不能混用不同版本的依赖。
程序不会自动下载安装包，也不修改剪映安装、注册表或原始草稿。

显式参数和 `JIANYING_INSTALL_DIR` / `JY_INSTALL_DIR` 优先，其后查找独立组件，
最后查找剪映安装目录。失败时仍可回退到其他已安装版本。
旧服务启动的解密子进程也会尝试独立组件；回退使用另一个子进程隔离 DLL，
因此无需重启正在处理剪辑任务的服务。Smart V3 与主读取程序共用实现。

## 本次恢复所用来源

- 版本：11.6.0.14552。
- [官方安装包](https://lf3-package.vlabstatic.com/obj/faceu-packages/Jianying_11_6_0_14552_jianyingpro_0_creatortool.exe)。
- [WinGet 校验清单](https://github.com/microsoft/winget-pkgs/blob/master/manifests/b/ByteDance/JianyingPro/11.6.0.14552/ByteDance.JianyingPro.installer.yaml)。
- 安装包 SHA256：`410441c0cb4150216127d6cda2e242d41a152f3e2e4a749227ea4af52215ca48`。

仅解压安装包，不执行安装或降级。提取同版本 DLL 及其递归导入依赖后保存到
上述独立目录；目录中的 `origin.json` 记录下载地址、安装包校验值和各 DLL 校验值。
第三方二进制与安装包不纳入 Git 或桌面安装包。

验证时既要检查单个解密结果是有效 JSON，也要绕过发现缓存重新扫描全部草稿。
空时间线仍报告无视频片段，不用历史缓存或备份内容冒充当前草稿。

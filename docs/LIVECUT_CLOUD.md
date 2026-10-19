# 云端连接设置（当前版本）

云端管理无需账号、密码或登录。进入「系统设置 → 运行环境 → 云端服务器」，填写 `http://192.168.1.240:8088` 并保存。地址持久保存在本机数据库，重新启动后自动读取并连接。支持 HTTP 和 HTTPS。

手机独立页面通过「系统设置」保存服务器地址到当前设备，重新打开自动连接。

后端启动无需 LIVECUT_PASSWORD。LIVECUT_USERNAME 仅作为兼容旧数据的归档标识，不参与登录，已有部署应保留其原值以继续查看旧数据。所有能访问服务器的客户端均可使用同一归档。

实际上传仍需要服务器配置七牛存储。

# 原版本部署说明

后端实现位于同级项目 `qianchuan-app/server/livecut.py`，部署说明位于该项目 `LIVECUT_CLOUD.md`。LiveCut 桌面侧边栏新增「云端管理」，独立共享界面位于 `web/cloud/`。服务器地址由用户在系统设置填写，不把七牛密钥写入安装包。

## 桌面

Windows：`npm run dist:win`。macOS：在 Mac 上执行 `npm run dist:mac`。安装包内置 Python 剪辑服务和 FFmpeg；云端模块和现有剪辑功能共存。本地成片接口只读取已有数据库，不初始化其他工作流。

## Android

共用 `web/cloud/`，不包含剪辑服务。`mobile/build.cjs` 只复制云端界面，并打包原生下载/分享桥接；`capacitor.config.json` 使用本地 HTTPS WebView，不远程加载应用代码。

依赖 Node 22+、JDK 21、Android SDK 36 及 build-tools 36.0.0（项目统一指定此版本）。设置 JAVA_HOME、ANDROID_HOME；创建本机 `android/local.properties` 指向 SDK（不提交）。

```powershell
npm install
npm run sync:android
cd android
.\gradlew.bat assembleDebug
```

调试 APK 位于 `android/app/build/outputs/apk/debug/app-debug.apk`。正式发布需要配置自己的 Android 签名密钥；不要把密钥加入仓库。现有 Android 壳仅提供云端查看、播放、上传、下载和记录，不包含电脑本地 API。

原生下载到应用缓存后调用系统分享面板选择保存位置/分享应用；关闭应用不会清理尚在 IndexedDB 的上传任务。上传期间应保持页面打开，失败任务手动重试。

## 验证

`npm run test:cloud` 使用 Edge 验证手机布局、登录、商品创建、失败上传持久化、重新打开和重试归档。`python -m unittest tests.test_cloud_catalog -q` 验证本地成片索引只读、缺失文件及任务标识。

## 第一版范围

共享归档、商品款号、手动批量归档、本机历史成片选择、七牛核验、私有播放下载、备注/使用状态、剪辑记录与统计。尚未包含自动全量任务同步、分块续传、后台上传、封面生成、多用户注册和云端删除。实际上传/播放需要先部署后端并配置七牛私有空间。

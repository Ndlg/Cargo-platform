# Cargo Platform 采集器

采集器安装在业务电脑上，只负责读取本机打印组件产生的原始打印任务，并上传到Cargo Platform。它不识别商品、不生成 Excel，也不处理 SKU 图片。

## 现场使用

后台下载包只包含两个文件：

```text
Cargo Platform 采集器.exe
参数说明.txt
```

业务机不需要 Python，也不需要输入系统账号密码。管理员先在网页后台生成采集器 token，再按参数启动。

## 推荐启动命令

复制前只需要替换两处：`<TOKEN>` 换成网页后台生成的采集器 token；`<服务器地址>` 换成系统访问地址，例如 `http://服务器IP:5173`，不要填写 8000 端口。

正式后台监听，最常用：

```powershell
Cargo Platform 采集器.exe --base-url "<服务器地址>" --token "<TOKEN>" --loop
```

指定后台显示名称，设备标识仍自动使用业务机机器名：

```powershell
Cargo Platform 采集器.exe --base-url "<服务器地址>" --token "<TOKEN>" --collector-name "Cargo Platform 采集器" --loop
```

先保存配置，再后台启动：

```powershell
Cargo Platform 采集器.exe --base-url "<服务器地址>" --token "<TOKEN>" --collector-name "Cargo Platform 采集器" --save-config
Cargo Platform 采集器.exe --loop
```

指定日志文件位置：

```powershell
Cargo Platform 采集器.exe --base-url "<服务器地址>" --token "<TOKEN>" --loop --log-file "%LOCALAPPDATA%\CargoPlatformCollector\collector.log"
```

只检查连接和本机打印组件，不持续监听：

```powershell
Cargo Platform 采集器.exe --base-url "<服务器地址>" --token "<TOKEN>" --check --log-file "%LOCALAPPDATA%\CargoPlatformCollector\collector-check.log"
```

`Cargo Platform 采集器.exe` 是无控制台窗口版本，按参数启动后不会弹黑框。服务器重启或断网时，采集器会留在后台等待；服务器恢复后会继续心跳和监听。

## 常用参数

```text
--base-url        服务器网页地址或服务器 IP；不要填写 8000 端口。
--token           后台生成的采集器 token，必填。
--loop            持续后台监听。
--collector-name  后台显示名称，默认是 Cargo Platform 采集器。
--interval        心跳和采集轮询间隔，默认 3 秒。
--config          可选配置文件路径，默认在 %LOCALAPPDATA%\CargoPlatformCollector。
--state           可选状态文件路径，默认和配置文件同目录。
--log-file        可选日志文件路径，默认和配置文件同目录 collector.log。
--save-config     保存当前 base-url/token/名称等配置后退出。
--check           检查本机打印组件和服务器心跳后退出。
```

用户不需要填写设备标识。采集器会自动读取业务机 Windows 机器名作为设备标识并上传。

默认日志位置：

```text
%LOCALAPPDATA%\CargoPlatformCollector\collector.log
```

## 默认读取的打印组件

```text
菜鸟打印组件：
C:\Program Files (x86)\CNPrintTool\resources\print.db

云打印 / 抖店相关打印客户端：
C:\Program Files (x86)\CloudPrintClient\resources\print.db
```

如果业务电脑没有安装对应组件，采集器会显示“未安装”。这不是采集器故障。

## 开发和打包

打包 Windows exe：

```bat
collector-client\build_windows_exe.bat
```

输出文件：

```text
collector-client\dist\Cargo Platform 采集器.exe
```

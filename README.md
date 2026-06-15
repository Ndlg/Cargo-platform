# Cargo Platform

Cargo Platform用于把业务机打印组件里的面单任务采集到服务器，再在网页里完成商品/SKU 识别、图片维护、档口分配和报货 Excel 导出。

本文按两类用户写：

```text
1. 只想部署使用的人：用 Docker 镜像，照着命令启动即可。
2. 要二次开发的人：从源码安装 Python、Node.js、数据库和前端环境。
```

普通用户请优先看“Docker 镜像部署”。

## 一、Docker 镜像部署

这种方式不需要安装 Python、Node.js，也不需要自己编译前端。

### 1. 准备一台服务器电脑

服务器电脑建议满足：

```text
Windows 10/11 或 Windows Server
内存 8GB 以上
硬盘剩余空间 20GB 以上
能被业务机通过局域网访问
```

需要提前安装：

```text
Docker Desktop
```

安装后打开 Docker Desktop，等左下角显示 Docker 正在运行。

### 2. 下载部署包

到 GitHub Release 下载：

```text
cargo-platform-deploy-v0.1.0.zip
```

解压到一个固定目录，例如：

```text
D:\\cargo-platform
```

目录里应有：

```text
docker-compose.release.yml
deploy.env.example
```

### 3. 生成配置文件

在解压目录空白处按住 Shift，右键打开 PowerShell，然后执行：

```powershell
copy deploy.env.example .env
```

打开 `.env`，至少修改这一项：

```text
SECRET_KEY=换成一串足够长的随机字符串
```

示例：

```text
SECRET_KEY=cargo-platform-change-this-to-a-long-random-value-2026
```

如果只是内网试用，也可以先不改，后续正式使用前再改。

### 4. 拉取镜像并启动

在部署目录执行：

```powershell
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

第一次会下载镜像，时间取决于网络。

### 5. 检查是否启动成功

执行：

```powershell
docker compose -f docker-compose.release.yml ps
```

看到下面几个服务都是 `running` 或 `Up` 就说明启动成功：

```text
cargo-platform-backend
cargo-platform-tenant-ui
cargo-platform-admin-ui
cargo-platform-redis
```

### 6. 打开系统

本机访问：

```text
客户业务页：http://127.0.0.1:5173
客户管理页：http://127.0.0.1:5173/admin
平台管理页：http://127.0.0.1:5174/admin
后端接口文档：http://127.0.0.1:8000/docs
```

业务机访问服务器时，把 `127.0.0.1` 换成服务器局域网 IP，例如：

```text
http://服务器IP:5173
```

不知道服务器 IP 时，在服务器 PowerShell 执行：

```powershell
ipconfig
```

找到当前网卡的 IPv4 地址。

### 7. 默认账号

初始管理员账号：

```text
账号：admin
密码：admin123
```

第一次进入平台管理页后，建议先创建客户账号，再用客户账号进入客户管理页维护业务资料。

### 8. 防火墙放行

业务机需要访问服务器的：

```text
5173 端口
```

如果业务机打不开 `http://服务器IP:5173`，优先检查服务器防火墙是否放行 5173。

平台管理页默认只绑定服务器本机：

```text
127.0.0.1:5174
```

不要把平台管理页直接暴露到公网。

### 9. 数据保存在哪里

Docker 部署的数据保存在 Docker 数据卷：

```text
cargo-platform-data
```

这里保存数据库、上传图片、采集记录和导出文件。

源码目录和部署包里不保存业务数据。

### 10. 停止系统

停止容器但保留数据：

```powershell
docker compose -f docker-compose.release.yml down
```

不要执行：

```powershell
docker compose -f docker-compose.release.yml down -v
```

`-v` 会删除数据卷，业务数据会丢失。

### 11. 查看日志

查看全部日志：

```powershell
docker compose -f docker-compose.release.yml logs -f
```

只看后端日志：

```powershell
docker compose -f docker-compose.release.yml logs -f backend
```

按 `Ctrl + C` 退出日志查看，不会停止系统。

### 12. 升级版本

下载新版本部署包，覆盖 `docker-compose.release.yml` 和 `deploy.env.example` 后执行：

```powershell
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

只要不删除 `cargo-platform-data` 数据卷，业务数据会继续保留。

## 二、业务机采集器

业务机不需要源码，也不需要 Docker。

从 GitHub Release 下载：

```text
cargo-platform-collector-v0.1.0-windows-x64.zip
```

解压后包含：

```text
Cargo Platform 采集器.exe
参数说明.txt
```

管理员在客户管理页创建采集器并生成 token，然后在业务机执行：

```powershell
Cargo Platform 采集器.exe --base-url "http://服务器IP:5173" --token "<TOKEN>" --loop
```

说明：

```text
服务器IP：部署Cargo Platform的服务器局域网 IP。
TOKEN：网页后台生成的采集器 token。
--loop：持续后台监听。
```

采集器会自动读取业务机 Windows 机器名作为设备标识。服务器重启或网络中断时，采集器会在后台等待，服务器恢复后继续连接。

## 三、要二次开发的人怎么启动

源码启动适合开发、调试和二次开发，不建议普通业务用户使用。

### 1. 准备开发环境

需要安装：

```text
Python 3.12+
Node.js 20+
MySQL 8 或兼容数据库
Redis 7
Git
```

### 2. 拉取源码

```powershell
git clone https://github.com/Ndlg/cargo-platform.git
cd cargo-platform
```

### 3. 初始化配置

```powershell
copy .env.example .env
mysql -u root -p < scripts/init_db.sql
```

检查 `.env` 里的数据库连接：

```text
DATABASE_URL=mysql+pymysql://cargo_user:cargo_pass@127.0.0.1:3306/cargo_platform
REDIS_URL=redis://127.0.0.1:6379/0
```

### 4. 启动后端

```powershell
scripts\start_backend.bat
```

后端地址：

```text
http://127.0.0.1:8000/docs
```

### 5. 启动客户前端

另开一个 PowerShell：

```powershell
scripts\start_frontend_dev.bat
```

访问：

```text
http://127.0.0.1:5173
http://127.0.0.1:5173/admin
```

### 6. 启动平台管理前端

另开一个 PowerShell：

```powershell
scripts\start_server_admin_frontend_dev.bat
```

访问：

```text
http://127.0.0.1:5174/admin
```

### 7. 运行测试

```powershell
python -m pytest backend/tests -q
```

### 8. 从源码构建 Docker 镜像

开发者也可以自己构建镜像：

```powershell
docker volume create cargo-platform-data
docker compose -p cargo-platform -f docker-compose.yml -f docker-compose.site.yml build
docker compose -p cargo-platform -f docker-compose.yml -f docker-compose.site.yml up -d
```

### 9. 构建采集器 exe

在 Windows 开发机执行：

```powershell
collector-client\build_windows_exe.bat
```

输出文件：

```text
collector-client\dist\Cargo Platform 采集器.exe
```

## 四、当前发布镜像

当前版本：

```text
v0.1.0
```

Docker 镜像：

```text
ghcr.io/ndlg/cargo-platform-backend:0.1.0
ghcr.io/ndlg/cargo-platform-tenant-ui:0.1.0
ghcr.io/ndlg/cargo-platform-admin-ui:0.1.0
```

也会同步维护：

```text
ghcr.io/ndlg/cargo-platform-backend:latest
ghcr.io/ndlg/cargo-platform-tenant-ui:latest
ghcr.io/ndlg/cargo-platform-admin-ui:latest
```

正式部署建议使用版本号标签，不建议长期依赖 `latest`。

## 五、发布者构建镜像

发布者需要先登录镜像仓库，例如 GitHub Container Registry：

```powershell
gh auth token | docker login ghcr.io -u <GitHub用户名> --password-stdin
```

构建本地镜像：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release_images.ps1 -Version 0.1.0
```

构建并推送镜像：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release_images.ps1 -Version 0.1.0 -Push
```

默认推送到：

```text
ghcr.io/ndlg/cargo-platform-backend
ghcr.io/ndlg/cargo-platform-tenant-ui
ghcr.io/ndlg/cargo-platform-admin-ui
```

如果后续换成组织账号，可以指定：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\release_images.ps1 -Version 0.1.0 -Registry ghcr.io/<组织名> -Push
```

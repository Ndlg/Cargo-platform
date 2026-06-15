# Cargo Platform 使用手册

Cargo Platform 用于把业务机打印组件里的面单任务采集到服务器，在网页里完成商品/SKU 识别、图片维护、档口分配和报货 Excel 导出。

本文分三类用户：

```text
1. Docker Desktop 用户：Windows 电脑，有图形界面，按步骤点开 Docker Desktop 后执行命令。
2. 命令行 Docker 用户：Windows Server、Linux 或熟悉命令行的用户。
3. 二次开发用户：需要改代码、调试接口、重新构建镜像的人。
```

普通使用者请优先选择 Docker 部署，不需要安装 Python、Node.js，也不需要自己编译前端。

## 一、准备部署包

到 GitHub Release 下载：

```text
cargo-platform-deploy-v0.1.0.zip
```

解压到固定目录，例如：

```text
D:\cargo-platform
```

解压后应看到：

```text
docker-compose.release.yml
deploy.env.example
README.md
```

复制环境配置：

```powershell
copy deploy.env.example .env
```

打开 `.env`，至少确认：

```text
CARGO_PLATFORM_VERSION=0.1.0
SECRET_KEY=replace-this-with-a-long-random-value
```

正式使用前建议把 `SECRET_KEY` 改成一串更长的随机字符串，例如：

```text
SECRET_KEY=cargo-platform-local-secret-please-change-2026-001
```

## 二、Docker Desktop 图形界面部署

适合 Windows 10/11 或有桌面的 Windows Server。

### 1. 安装 Docker Desktop

1. 下载并安装 Docker Desktop。
2. 安装时保持默认选项即可。
3. 安装完成后重启电脑。
4. 打开 Docker Desktop。
5. 等左下角显示 Docker 已运行。

如果 Docker Desktop 提示启用 WSL2，按提示启用即可。

### 2. 打开 PowerShell

进入部署目录：

```powershell
cd D:\cargo-platform
```

确认 Docker 可用：

```powershell
docker version
docker compose version
```

只要能显示版本号，就说明 Docker 命令可用。

### 3. 拉取镜像

```powershell
docker compose -f docker-compose.release.yml pull
```

第一次会从网络下载镜像，速度取决于网络。

如果下载失败，先确认：

```text
Docker Desktop 正在运行
电脑可以访问 GitHub Container Registry
网络代理或防火墙没有拦截 Docker
```

### 4. 启动系统

```powershell
docker compose -f docker-compose.release.yml up -d
```

启动完成后等待 10 到 30 秒。

### 5. 检查容器

命令检查：

```powershell
docker compose -f docker-compose.release.yml ps
```

正常应看到这些容器是 `Up` 或 `running`：

```text
cargo-platform-backend
cargo-platform-tenant-ui
cargo-platform-admin-ui
cargo-platform-redis
```

Docker Desktop 图形界面检查：

1. 打开 Docker Desktop。
2. 进入 Containers。
3. 找到 `cargo-platform`。
4. 展开后能看到 backend、tenant-ui、admin-ui、redis。
5. 它们左侧是绿色运行状态即可。

### 6. 打开网页

在服务器本机打开：

```text
客户业务页：http://127.0.0.1:5173
客户管理页：http://127.0.0.1:5173/admin
平台管理页：http://127.0.0.1:5174/admin
后端接口文档：http://127.0.0.1:8000/docs
```

其他业务机访问时，把 `127.0.0.1` 换成服务器局域网 IP：

```text
http://服务器IP:5173
```

不知道服务器 IP 时，在服务器 PowerShell 执行：

```powershell
ipconfig
```

找到当前网卡的 IPv4 地址。

### 7. 首次登录

初始平台管理员：

```text
账号：admin
密码：admin123
```

建议流程：

```text
1. 先进入平台管理页：http://127.0.0.1:5174/admin
2. 创建客户账号和客户工作空间。
3. 再用客户账号登录客户管理页：http://服务器IP:5173/admin
4. 在客户管理页维护商品、SKU、档口、识别规则和采集器。
```

平台管理页默认只允许服务器本机访问，不建议暴露到公网。

### 8. 放行防火墙

业务机需要访问服务器：

```text
5173 端口
```

如果业务机打不开 `http://服务器IP:5173`，在服务器防火墙里放行 TCP 5173。

后端 8000 和平台管理 5174 默认绑定本机，不需要给业务机开放。

## 三、命令行 Docker 部署

适合 Linux、Windows Server Core、远程 SSH 或习惯命令行的用户。

### 1. 安装 Docker

需要：

```text
Docker Engine
Docker Compose v2
```

确认：

```bash
docker version
docker compose version
```

### 2. 准备部署目录

```bash
mkdir -p /opt/cargo-platform
cd /opt/cargo-platform
```

把 Release 里的文件放到该目录：

```text
docker-compose.release.yml
deploy.env.example
README.md
```

生成 `.env`：

```bash
cp deploy.env.example .env
```

编辑 `.env`：

```bash
nano .env
```

至少修改 `SECRET_KEY`。

### 3. 拉取镜像

```bash
docker compose -f docker-compose.release.yml pull
```

### 4. 启动系统

```bash
docker compose -f docker-compose.release.yml up -d
```

### 5. 检查状态

```bash
docker compose -f docker-compose.release.yml ps
```

### 6. 查看日志

查看全部日志：

```bash
docker compose -f docker-compose.release.yml logs -f
```

只看后端：

```bash
docker compose -f docker-compose.release.yml logs -f backend
```

按 `Ctrl + C` 退出日志查看，不会停止系统。

### 7. 停止系统

停止容器但保留数据：

```bash
docker compose -f docker-compose.release.yml down
```

不要执行：

```bash
docker compose -f docker-compose.release.yml down -v
```

`-v` 会删除数据卷，业务数据会丢失。

## 四、拉取容器后还要做什么

镜像拉取并启动后，不是结束，还需要做这些：

```text
1. 打开 http://服务器IP:5173，确认页面能访问。
2. 打开 http://127.0.0.1:5174/admin，登录平台管理。
3. 创建客户账号和工作空间。
4. 使用客户账号登录 http://服务器IP:5173/admin。
5. 在客户管理页维护商品/SKU、档口和识别规则。
6. 在客户管理页创建采集器并生成 token。
7. 到业务机安装 Cargo Platform 采集器。
8. 业务机采集器连上后，在业务页面开始采集、识别和导出报货表。
```

## 五、业务机采集器

业务机不需要 Docker，也不需要源码。

从 Release 下载：

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
服务器IP：部署 Cargo Platform 的服务器局域网 IP。
TOKEN：网页后台生成的采集器 token。
--loop：持续后台监听。
```

采集器会自动读取业务机 Windows 机器名作为设备标识。服务器重启或网络中断时，采集器会在后台等待，服务器恢复后继续连接。

## 六、数据保存在哪里

Docker 部署的数据保存在 Docker 数据卷：

```text
cargo-platform-data
```

里面包含：

```text
数据库
上传图片
采集记录
导出文件
```

源码目录和部署包里不保存业务数据。

不要删除这个数据卷。

## 七、升级版本

下载新版本部署包，覆盖：

```text
docker-compose.release.yml
deploy.env.example
```

保留原来的 `.env`，然后执行：

```powershell
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

数据卷 `cargo-platform-data` 会继续复用。

## 八、二次开发启动

二次开发用户需要安装：

```text
Python 3.12+
Node.js 20+
MySQL 8 或兼容数据库
Redis 7
Git
```

拉取源码：

```powershell
git clone https://github.com/Ndlg/Cargo-platform.git
cd Cargo-platform
```

初始化配置：

```powershell
copy .env.example .env
mysql -u root -p < scripts/init_db.sql
```

检查 `.env`：

```text
DATABASE_URL=mysql+pymysql://cargo_user:cargo_pass@127.0.0.1:3306/cargo_platform
REDIS_URL=redis://127.0.0.1:6379/0
```

启动后端：

```powershell
scripts\start_backend.bat
```

启动客户前端：

```powershell
scripts\start_frontend_dev.bat
```

启动平台管理前端：

```powershell
scripts\start_server_admin_frontend_dev.bat
```

运行测试：

```powershell
python -m pytest backend/tests -q
```

## 九、二次开发者构建 Docker 镜像

开发者可以自己从源码构建镜像：

```powershell
docker volume create cargo-platform-data
docker compose -p cargo-platform -f docker-compose.yml -f docker-compose.site.yml build
docker compose -p cargo-platform -f docker-compose.yml -f docker-compose.site.yml up -d
```

发布者构建并推送镜像：

```powershell
gh auth token | docker login ghcr.io -u <GitHub用户名> --password-stdin
powershell -ExecutionPolicy Bypass -File scripts\release_images.ps1 -Version 0.1.0 -Push
```

也可以直接使用仓库里的 GitHub Actions：

```text
GitHub 仓库 -> Actions -> Release Images -> Run workflow -> 输入版本号
```

或者推送版本标签后自动构建：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

默认镜像：

```text
ghcr.io/ndlg/cargo-platform-backend:0.1.0
ghcr.io/ndlg/cargo-platform-tenant-ui:0.1.0
ghcr.io/ndlg/cargo-platform-admin-ui:0.1.0
```

正式部署建议固定版本号标签，不建议长期依赖 `latest`。

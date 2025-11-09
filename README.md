# AstrBot New-API 签到抽奖插件

这是一个用于 AstrBot 的插件，可以让用户绑定 New-API 账号，通过每日签到和抽奖获取额度。

## 功能

- ✅ 绑定 New-API 账号（双向唯一绑定：一个 QQ 只能绑定一个账号，一个账号只能被一个 QQ 绑定）
- ✅ 每日签到获取额度
- ✅ 抽奖系统（管理员可开启/关闭，支持自定义奖项和概率）
- ✅ 查看绑定状态和账号余额
- ✅ 基于北京时间的日期判断（每天0点刷新）

## 安装

1. 将插件文件夹放入 AstrBot 的 `data/plugins/` 目录
2. 在 AstrBot 容器中安装依赖：
```bash
docker exec -it astrbot pip install asyncpg bcrypt
```
3. 在 AstrBot WebUI 中配置插件参数
4. 重新加载插件

## 使用方法

### 用户命令

#### 绑定账号
```
/绑定 <账号> <密码>
```
示例：`/绑定 myuser mypassword`

**⚠️ 重要提示**：
- 建议在私聊中使用此命令，避免密码泄露
- 绑定后无法解绑，请谨慎操作
- 一个 QQ 只能绑定一个账号，一个账号只能被一个 QQ 绑定

#### 每日签到
```
/签到
```
每天北京时间0点刷新，可重新签到

#### 参与抽奖
```
/抽奖
```
需要管理员开启抽奖功能后才能使用

#### 查看绑定状态
```
/我的绑定
```

#### 查看账号余额
```
/查看余额
```

#### 查看抽奖状态
```
/抽奖状态
```
显示抽奖开关状态、剩余次数、奖项列表和中奖概率

#### 显示功能菜单
```
/New-API
```

### 管理员命令

#### 开启抽奖
```
/开启抽奖
```
需要群管理员/群主权限

#### 关闭抽奖
```
/关闭抽奖
```
需要群管理员/群主权限

## 配置说明

在 AstrBot WebUI 的插件配置页面中可以设置：

### 数据库配置
- `database_host`: New-API PostgreSQL 数据库主机（默认：localhost）
- `database_port`: 数据库端口（默认：5432）
- `database_user`: 数据库用户名（默认：postgres）
- `database_password`: 数据库密码（需要填写）
- `database_name`: 数据库名称（默认：new-api）

### 签到配置
- `checkin_quota`: 每日签到获得的额度（默认：500000，即 $1）
- `enable_daily_limit`: 是否启用每日签到限制（默认：true）

### 抽奖配置
- `lottery_enabled`: 是否开启抽奖功能（默认：false，需管理员手动开启）
- `lottery_daily_limit`: 每日抽奖次数限制（默认：1）
- `lottery_prizes`: 抽奖奖项配置（JSON格式）

#### 抽奖奖项配置示例
```json
[
  {"quota":1000000,"weight":5,"name":"超级大奖"},
  {"quota":500000,"weight":15,"name":"大奖"},
  {"quota":100000,"weight":50,"name":"普通奖"},
  {"quota":0,"weight":30,"name":"谢谢参与"}
]
```
- `quota`: 奖励额度（500000=$1）
- `weight`: 权重（用于计算概率）
- `name`: 奖项名称

## 额度说明

New-API 中的额度单位：
- 500000 = $1.00
- 250000 = $0.50
- 100000 = $0.20

## 技术实现

- 使用 SQLite 存储 QQ 号与 New-API 账号的绑定关系及抽奖记录
- 使用 asyncpg 连接 PostgreSQL 数据库进行账号验证和额度操作
- 使用 bcrypt 验证密码

## 许可证

MIT License

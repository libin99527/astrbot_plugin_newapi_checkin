from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.star_tools import StarTools
from astrbot.core.star.filter.permission import PermissionType
import asyncpg
import sqlite3
import re
import bcrypt
import random
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 北京时区 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))


@register("astrbot_plugin_newapi_checkin", "Claude", "New-API 签到抽奖插件", "v1.0.0")
class NewAPICheckinPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        # 获取插件配置
        self.db_host = config.get("database_host", "localhost")
        self.db_port = config.get("database_port", 5432)
        self.db_user = config.get("database_user", "postgres")
        self.db_password = config.get("database_password", "")
        self.db_name = config.get("database_name", "new-api")
        self.checkin_quota = config.get("checkin_quota", 500000)
        self.enable_daily_limit = config.get("enable_daily_limit", True)

        # 抽奖配置
        self.lottery_enabled = config.get("lottery_enabled", False)
        self.lottery_daily_limit = config.get("lottery_daily_limit", 1)
        lottery_prizes_str = config.get("lottery_prizes", '[{"quota":1000000,"weight":5,"name":"超级大奖"},{"quota":500000,"weight":15,"name":"大奖"},{"quota":100000,"weight":50,"name":"普通奖"},{"quota":0,"weight":30,"name":"谢谢参与"}]')
        try:
            self.lottery_prizes = json.loads(lottery_prizes_str)
        except:
            logger.error(f"抽奖奖项配置解析失败，使用默认配置")
            self.lottery_prizes = [
                {"quota": 1000000, "weight": 5, "name": "超级大奖"},
                {"quota": 500000, "weight": 15, "name": "大奖"},
                {"quota": 100000, "weight": 50, "name": "普通奖"},
                {"quota": 0, "weight": 30, "name": "谢谢参与"}
            ]

        # 初始化本地数据库
        data_dir = StarTools.get_data_dir("astrbot_plugin_newapi_checkin")
        self.db_file = data_dir / "bindings.db"
        self._init_local_db()

        logger.info("New-API 签到插件已加载")

    def _init_local_db(self):
        """初始化本地 SQLite 数据库"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 绑定表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qq_bindings (
                qq_id TEXT PRIMARY KEY,
                newapi_username TEXT NOT NULL UNIQUE,
                bind_time INTEGER NOT NULL,
                last_checkin INTEGER
            )
        """)
        # 创建唯一索引确保一个账号只能被一个 QQ 绑定
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_newapi_username
            ON qq_bindings(newapi_username)
        """)

        # 抽奖记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lottery_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id TEXT NOT NULL,
                prize_name TEXT NOT NULL,
                prize_quota INTEGER NOT NULL,
                lottery_time INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lottery_qq_time
            ON lottery_records(qq_id, lottery_time)
        """)

        conn.commit()
        conn.close()
        logger.info(f"本地数据库初始化完成: {self.db_file}")

    async def _get_pg_connection(self):
        """获取 PostgreSQL 连接"""
        try:
            conn = await asyncpg.connect(
                host=self.db_host,
                port=self.db_port,
                user=self.db_user,
                password=self.db_password,
                database=self.db_name
            )
            return conn
        except Exception as e:
            logger.error(f"连接 PostgreSQL 失败: {e}")
            return None

    async def _verify_account(self, username: str, password: str) -> bool:
        """验证 New-API 账号密码"""
        conn = await self._get_pg_connection()
        if not conn:
            return False

        try:
            # 查询用户
            result = await conn.fetchrow(
                "SELECT id, password FROM users WHERE username = $1 AND deleted_at IS NULL",
                username
            )

            if not result:
                return False

            # 验证密码（使用 bcrypt）
            stored_password = result["password"]
            return bcrypt.checkpw(password.encode(), stored_password.encode())
        except Exception as e:
            logger.error(f"验证账号失败: {e}")
            return False
        finally:
            await conn.close()

    async def _add_quota(self, username: str, quota: int) -> bool:
        """给 New-API 账号增加额度"""
        conn = await self._get_pg_connection()
        if not conn:
            return False

        try:
            result = await conn.execute(
                "UPDATE users SET quota = quota + $1 WHERE username = $2 AND deleted_at IS NULL",
                quota,
                username
            )
            return result == "UPDATE 1"
        except Exception as e:
            logger.error(f"增加额度失败: {e}")
            return False
        finally:
            await conn.close()

    async def _get_quota(self, username: str):
        """查询 New-API 账号的额度"""
        conn = await self._get_pg_connection()
        if not conn:
            return None

        try:
            result = await conn.fetchrow(
                "SELECT quota, used_quota FROM users WHERE username = $1 AND deleted_at IS NULL",
                username
            )
            if result:
                return {
                    "quota": result["quota"],
                    "used_quota": result["used_quota"]
                }
            return None
        except Exception as e:
            logger.error(f"查询额度失败: {e}")
            return None
        finally:
            await conn.close()

    def _get_binding_by_username(self, username: str):
        """通过用户名获取绑定信息"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT qq_id, bind_time FROM qq_bindings WHERE newapi_username = ?", (username,))
        result = cursor.fetchone()
        conn.close()
        return result

    def _get_binding(self, qq_id: str):
        """获取 QQ 绑定信息"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT newapi_username, bind_time, last_checkin FROM qq_bindings WHERE qq_id = ?", (qq_id,))
        result = cursor.fetchone()
        conn.close()
        return result

    def _save_binding(self, qq_id: str, username: str):
        """保存 QQ 绑定"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO qq_bindings (qq_id, newapi_username, bind_time, last_checkin) VALUES (?, ?, ?, NULL)",
            (qq_id, username, int(datetime.now().timestamp()))
        )
        conn.commit()
        conn.close()

    def _update_checkin_time(self, qq_id: str):
        """更新签到时间"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE qq_bindings SET last_checkin = ? WHERE qq_id = ?",
            (int(datetime.now().timestamp()), qq_id)
        )
        conn.commit()
        conn.close()

    def _can_checkin(self, last_checkin: int) -> bool:
        """检查是否可以签到（基于北京时间）"""
        if not self.enable_daily_limit:
            return True

        if last_checkin is None:
            return True

        # 使用北京时间进行判断
        last_time = datetime.fromtimestamp(last_checkin, tz=BEIJING_TZ)
        now = datetime.now(tz=BEIJING_TZ)

        # 检查是否是同一天
        return last_time.date() < now.date()

    def _get_lottery_count_today(self, qq_id: str) -> int:
        """获取今天的抽奖次数（基于北京时间）"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        # 获取北京时间今天开始的时间戳
        now_beijing = datetime.now(tz=BEIJING_TZ)
        today_start = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
        today_timestamp = int(today_start.timestamp())

        cursor.execute(
            "SELECT COUNT(*) FROM lottery_records WHERE qq_id = ? AND lottery_time >= ?",
            (qq_id, today_timestamp)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def _record_lottery(self, qq_id: str, prize_name: str, prize_quota: int):
        """记录抽奖"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lottery_records (qq_id, prize_name, prize_quota, lottery_time) VALUES (?, ?, ?, ?)",
            (qq_id, prize_name, prize_quota, int(datetime.now().timestamp()))
        )
        conn.commit()
        conn.close()

    def _perform_lottery(self) -> dict:
        """执行抽奖（加权随机）"""
        if not self.lottery_prizes:
            return None

        total_weight = sum(prize["weight"] for prize in self.lottery_prizes)
        if total_weight == 0:
            return None

        rand_val = random.uniform(0, total_weight)
        current_weight = 0

        for prize in self.lottery_prizes:
            current_weight += prize["weight"]
            if rand_val <= current_weight:
                return prize

        return self.lottery_prizes[-1]  # 兜底返回最后一个

    @filter.command("绑定")
    async def bind_account(self, event: AstrMessageEvent):
        """绑定 New-API 账号
        用法：/绑定 <账号> <密码>
        """
        # 解析命令参数
        match = re.match(r"绑定\s+(\S+)\s+(\S+)", event.message_str)
        if not match:
            yield event.plain_result("❌ 格式错误\n正确用法：/绑定 <账号> <密码>\n示例：/绑定 myuser mypassword")
            return

        username = match.group(1)
        password = match.group(2)
        qq_id = event.get_sender_id()

        # 检查当前 QQ 是否已绑定
        existing = self._get_binding(qq_id)
        if existing:
            yield event.plain_result(f"❌ 你已经绑定了账号：{existing[0]}\n如需更换，请先使用 /解绑 命令")
            return

        # 检查该账号是否已被其他 QQ 绑定
        existing_bind = self._get_binding_by_username(username)
        if existing_bind:
            yield event.plain_result(f"❌ 该账号已被其他用户绑定\n每个 New-API 账号只能绑定一个 QQ")
            return

        # 验证账号密码
        yield event.plain_result("🔄 正在验证账号...")
        is_valid = await self._verify_account(username, password)

        if not is_valid:
            yield event.plain_result("❌ 账号或密码错误，请检查后重试")
            return

        # 保存绑定
        self._save_binding(qq_id, username)
        yield event.plain_result(f"✅ 绑定成功！\n账号：{username}\n现在可以使用 /签到 命令获取每日额度啦~")

    @filter.command("签到")
    async def checkin(self, event: AstrMessageEvent):
        """每日签到获取额度"""
        async for result in self._do_checkin(event):
            yield result

    async def _do_checkin(self, event: AstrMessageEvent):
        """签到的实际执行逻辑"""
        qq_id = event.get_sender_id()

        # 检查是否已绑定
        binding = self._get_binding(qq_id)
        if not binding:
            yield event.plain_result("❌ 你还没有绑定账号\n请使用 /绑定 <账号> <密码> 进行绑定")
            return

        username, bind_time, last_checkin = binding

        # 检查是否可以签到
        if not self._can_checkin(last_checkin):
            yield event.plain_result("❌ 你今天已经签到过了，明天再来吧~")
            return

        # 增加额度
        yield event.plain_result("🔄 正在签到...")
        success = await self._add_quota(username, self.checkin_quota)

        if not success:
            yield event.plain_result("❌ 签到失败，请稍后重试")
            return

        # 更新签到时间
        self._update_checkin_time(qq_id)

        # 计算额度（转换为美元）
        quota_dollars = self.checkin_quota / 500000
        yield event.plain_result(
            f"✅ 签到成功！\n"
            f"账号：{username}\n"
            f"获得额度：${quota_dollars:.2f}\n"
            f"{'明天记得再来签到哦~' if self.enable_daily_limit else '可以继续签到~'}"
        )

    @filter.command("我的绑定")
    async def my_binding(self, event: AstrMessageEvent):
        """查看绑定状态"""
        qq_id = event.get_sender_id()

        binding = self._get_binding(qq_id)
        if not binding:
            yield event.plain_result("❌ 你还没有绑定账号\n使用 /绑定 <账号> <密码> 进行绑定")
            return

        username, bind_time, last_checkin = binding
        bind_date = datetime.fromtimestamp(bind_time).strftime("%Y-%m-%d %H:%M:%S")

        if last_checkin:
            last_date = datetime.fromtimestamp(last_checkin).strftime("%Y-%m-%d")
            can_checkin = self._can_checkin(last_checkin)
            status = "✅ 今日可签到" if can_checkin else "❌ 今日已签到"
        else:
            last_date = "从未签到"
            status = "✅ 今日可签到"

        yield event.plain_result(
            f"📋 绑定信息\n"
            f"账号：{username}\n"
            f"绑定时间：{bind_date}\n"
            f"上次签到：{last_date}\n"
            f"签到状态：{status}"
        )

    @filter.command("查看余额")
    async def check_balance(self, event: AstrMessageEvent):
        """查看 New-API 账号余额"""
        qq_id = event.get_sender_id()

        # 检查是否已绑定
        binding = self._get_binding(qq_id)
        if not binding:
            yield event.plain_result("❌ 你还没有绑定账号\n请使用 /绑定 <账号> <密码> 进行绑定")
            return

        username = binding[0]

        # 查询余额
        yield event.plain_result("🔄 正在查询余额...")
        quota_info = await self._get_quota(username)

        if not quota_info:
            yield event.plain_result("❌ 查询失败，请稍后重试")
            return

        # 计算额度（转换为美元）
        quota_dollars = quota_info["quota"] / 500000
        used_dollars = quota_info["used_quota"] / 500000
        remaining_dollars = quota_dollars

        yield event.plain_result(
            f"💰 账号余额\n"
            f"账号：{username}\n"
            f"总额度：${quota_dollars:.2f}\n"
            f"已使用：${used_dollars:.2f}\n"
            f"剩余额度：${remaining_dollars:.2f}"
        )

    @filter.command("抽奖")
    async def lottery(self, event: AstrMessageEvent):
        """参与抽奖"""
        qq_id = event.get_sender_id()

        # 检查抽奖是否开启
        if not self.lottery_enabled:
            yield event.plain_result("❌ 抽奖功能未开启\n请联系管理员开启抽奖")
            return

        # 检查是否已绑定
        binding = self._get_binding(qq_id)
        if not binding:
            yield event.plain_result("❌ 你还没有绑定账号\n请先使用 /绑定 <账号> <密码> 进行绑定")
            return

        username = binding[0]

        # 检查今日抽奖次数
        lottery_count = self._get_lottery_count_today(qq_id)
        if lottery_count >= self.lottery_daily_limit:
            yield event.plain_result(f"❌ 你今天已经抽奖 {lottery_count} 次了\n每天最多抽奖 {self.lottery_daily_limit} 次，明天再来吧~")
            return

        # 执行抽奖
        yield event.plain_result("🎰 正在抽奖...")
        prize = self._perform_lottery()

        if not prize:
            yield event.plain_result("❌ 抽奖失败，请稍后重试")
            return

        # 记录抽奖
        self._record_lottery(qq_id, prize["name"], prize["quota"])

        # 如果有额度奖励，增加额度
        if prize["quota"] > 0:
            success = await self._add_quota(username, prize["quota"])
            if not success:
                yield event.plain_result(f"🎉 恭喜抽中【{prize['name']}】！\n但增加额度失败，请联系管理员")
                return

            quota_dollars = prize["quota"] / 500000
            yield event.plain_result(
                f"🎉 恭喜抽中【{prize['name']}】！\n"
                f"获得额度：${quota_dollars:.2f}\n"
                f"剩余抽奖次数：{self.lottery_daily_limit - lottery_count - 1}/{self.lottery_daily_limit}"
            )
        else:
            yield event.plain_result(
                f"😢 很遗憾，抽中了【{prize['name']}】\n"
                f"再接再厉，明天继续加油！\n"
                f"剩余抽奖次数：{self.lottery_daily_limit - lottery_count - 1}/{self.lottery_daily_limit}"
            )

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("开启抽奖")
    async def enable_lottery(self, event: AstrMessageEvent):
        """管理员开启抽奖"""
        self.lottery_enabled = True
        yield event.plain_result("✅ 抽奖功能已开启！\n用户可以使用 /抽奖 命令参与抽奖啦~")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("关闭抽奖")
    async def disable_lottery(self, event: AstrMessageEvent):
        """管理员关闭抽奖"""
        self.lottery_enabled = False
        yield event.plain_result("✅ 抽奖功能已关闭")

    @filter.command("抽奖状态")
    async def lottery_status(self, event: AstrMessageEvent):
        """查看抽奖状态"""
        status = "✅ 已开启" if self.lottery_enabled else "❌ 已关闭"
        qq_id = event.get_sender_id()
        lottery_count = self._get_lottery_count_today(qq_id)

        message = f"🎰 抽奖功能状态：{status}\n"
        message += f"📊 每日抽奖限制：{self.lottery_daily_limit} 次\n"
        message += f"🎯 今日已抽奖：{lottery_count} 次\n"
        message += f"💫 剩余抽奖次数：{self.lottery_daily_limit - lottery_count} 次\n\n"
        message += "🎁 奖项列表：\n"

        total_weight = sum(p["weight"] for p in self.lottery_prizes)
        for prize in self.lottery_prizes:
            prob = (prize["weight"] / total_weight * 100) if total_weight > 0 else 0
            quota_dollars = prize["quota"] / 500000
            if prize["quota"] > 0:
                message += f"  • {prize['name']}：${quota_dollars:.2f} (概率 {prob:.1f}%)\n"
            else:
                message += f"  • {prize['name']}：无奖励 (概率 {prob:.1f}%)\n"

        yield event.plain_result(message)

    @filter.command("New-API")
    async def show_menu(self, event: AstrMessageEvent):
        """显示插件功能菜单"""
        lottery_status = "✅ 已开启" if self.lottery_enabled else "❌ 已关闭"
        menu_text = (
            "📌 账号管理\n"
            "  /绑定 用户名 密码\n"
            "  /我的绑定\n\n"
            "💰 额度功能\n"
            "  /签到\n"
            "  /查看余额\n\n"
            f"🎰 抽奖功能 ({lottery_status})\n"
            "  /抽奖\n"
            "  /抽奖状态\n\n"
            "💡 使用 /New-API 查看此菜单\n"
            "⚠️ 建议私聊使用绑定功能"
        )
        yield event.plain_result(menu_text)

    async def terminate(self):
        """插件终止时"""
        logger.info("New-API 签到插件已终止")

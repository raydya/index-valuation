.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
BOOTSTRAP_PYTHON ?= python3

.PHONY: help env install setup db-up db-down db-logs db-shell migrate db-check check test test-db constituents sources daily schedule web backfill-index components-daily

help:
	@echo "make setup     安装依赖、启动 PostgreSQL、迁移并检查连接"
	@echo "make install   根据 uv.lock 安装 Python 依赖"
	@echo "make db-up     启动 PostgreSQL 并等待连接就绪"
	@echo "make db-down   停止 PostgreSQL，保留数据卷"
	@echo "make migrate   将数据库迁移到最新版本"
	@echo "make db-check  检查数据库连接"
	@echo "make check     检查代码格式和静态规则"
	@echo "make test      运行不依赖数据库的测试"
	@echo "make test-db   运行 PostgreSQL 集成测试"
	@echo "make constituents 同步所有指数成分和权重"
	@echo "make sources   检查富途行情权限和独立核验源"
	@echo "make daily     增量更新所有指数的点位、PE/PB/PS 和估值带"
	@echo "make backfill-index 全量回填所有指数的可用历史数据"
	@echo "make web       启动估值网页 http://127.0.0.1:8000"
	@echo "make components-daily 运行可选的成分股重算流程"
	@echo "make schedule  生成 macOS 每日任务配置（安装见 README）"

env:
	$(BOOTSTRAP_PYTHON) scripts/init_env.py

install:
	$(UV) sync --locked

setup:
	$(MAKE) env
	$(MAKE) install
	$(MAKE) db-up
	$(MAKE) migrate
	$(MAKE) db-check

db-up: env
	$(COMPOSE) up -d db
	$(UV) run --locked index-valuation check-db --wait 60

db-down:
	$(COMPOSE) down

db-logs:
	$(COMPOSE) logs --tail=100 db

db-shell:
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

migrate:
	$(UV) run --locked alembic upgrade head

db-check:
	$(UV) run --locked index-valuation check-db

check:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .

test:
	$(UV) run --locked pytest -m "not integration"

test-db:
	$(UV) run --locked pytest -m integration

constituents:
	$(UV) run --locked index-valuation sync-constituents --all

sources:
	$(UV) run --locked index-valuation check-sources --all

daily:
	$(UV) run --locked index-valuation update-index --all

components-daily:
	$(UV) run --locked index-valuation run-daily --all

backfill-index:
	$(UV) run --locked index-valuation backfill-index --all

web:
	$(UV) run --locked index-valuation serve

schedule:
	$(UV) run --locked python scripts/install_schedule.py

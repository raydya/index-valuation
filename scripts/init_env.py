"""Create a connection template without replacing an existing .env."""

import os
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    content = (root / ".env.example").read_text(encoding="utf-8")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("保留已有 .env 配置。")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    print("已创建 .env；请填入 local-infra 分配的运行与迁移账号凭据后再执行 make setup。")


if __name__ == "__main__":
    main()

"""Create local development settings without replacing an existing .env."""

import os
import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace-with-local-password", secrets.token_hex(24))
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("保留已有 .env 配置。")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    print("已创建 .env 并生成本地数据库密码。")


if __name__ == "__main__":
    main()

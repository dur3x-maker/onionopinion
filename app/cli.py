from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.security import hash_password, validate_login, validate_password
from app.web import clean_display_name


def create_admin(login: str, display_name: str) -> None:
    login = login.strip().lower()
    error = validate_login(login)
    if error:
        raise SystemExit(error)
    try:
        display_name = clean_display_name(display_name)
    except Exception as exc:
        raise SystemExit(str(getattr(exc, "detail", exc))) from exc
    password = getpass.getpass("Пароль администратора: ")
    confirmation = getpass.getpass("Повторите пароль: ")
    if password != confirmation:
        raise SystemExit("Пароли не совпадают.")
    error = validate_password(password)
    if error:
        raise SystemExit(error)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.login == login)):
            raise SystemExit("Пользователь с таким логином уже существует.")
        db.add(
            User(
                login=login,
                display_name=display_name,
                password_hash=hash_password(password),
                is_admin=True,
            )
        )
        db.commit()
    print("Администратор создан.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("create-admin", help="Создать администратора")
    command.add_argument("--login", required=True)
    command.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.login, args.display_name)


if __name__ == "__main__":
    main()


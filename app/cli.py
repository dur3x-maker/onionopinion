from __future__ import annotations

import argparse
import getpass

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Rule, RuleVersion, User
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


def add_rule_version(code: str, title: str, text: str) -> int:
    code = code.strip().upper()
    title = title.strip()
    text = text.strip()
    if not title or len(title) > 120:
        raise SystemExit("Название правила должно содержать от 1 до 120 символов.")
    if not text:
        raise SystemExit("Текст правила не может быть пустым.")
    with SessionLocal() as db:
        rule = db.scalar(select(Rule).where(Rule.code == code).with_for_update())
        if not rule:
            raise SystemExit("Правило не найдено.")
        number = (
            db.scalar(
                select(func.max(RuleVersion.version)).where(RuleVersion.rule_id == rule.id)
            )
            or 0
        ) + 1
        version = RuleVersion(
            rule_id=rule.id,
            version=number,
            title=title,
            text=text,
        )
        db.add(version)
        db.flush()
        rule.current_version_id = version.id
        db.commit()
        version_id = version.id
    print(f"Создана {code} version {number} (id={version_id}).")
    return version_id


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("create-admin", help="Создать администратора")
    command.add_argument("--login", required=True)
    command.add_argument("--display-name", required=True)
    version_command = commands.add_parser(
        "add-rule-version", help="Создать неизменяемую новую версию правила"
    )
    version_command.add_argument("--code", required=True)
    version_command.add_argument("--title", required=True)
    version_command.add_argument("--text", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.login, args.display_name)
    elif args.command == "add-rule-version":
        add_rule_version(args.code, args.title, args.text)


if __name__ == "__main__":
    main()

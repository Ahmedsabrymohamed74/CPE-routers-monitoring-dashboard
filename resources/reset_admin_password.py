"""Operator recovery command; never accepts passwords in command-line arguments."""
import argparse
import getpass
import os
import stat

from auth_store import AccountStore


def main():
    parser = argparse.ArgumentParser(description="Reset an existing dashboard administrator password.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-file", help="Read the new password from an operator-owned file with mode 600.")
    args = parser.parse_args()
    if args.password_file:
        with open(args.password_file, encoding="utf-8") as password_file:
            info = os.fstat(password_file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                parser.error("Password file must be a regular file owned by this user, accessible only to its owner.")
            password = password_file.read(1024).rstrip("\r\n")
    else:
        password = getpass.getpass("New dashboard password: ")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords do not match.")
    try:
        AccountStore().reset_admin_password(args.username, password)
    except ValueError as exc:
        parser.error(str(exc))
    print("Administrator password reset. Existing sessions revoked.")


if __name__ == "__main__":
    main()

from src.service import refresh_all
from src.db import DEFAULT_DB


def main() -> None:
    for row in refresh_all(DEFAULT_DB):
        print(f"{row.get('source')}\t{row.get('status')}\t{row.get('message', '')}")


if __name__ == "__main__":
    main()

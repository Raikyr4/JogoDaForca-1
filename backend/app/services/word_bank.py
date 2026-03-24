import random
from pathlib import Path


class WordBank:
    def __init__(self, words_file: str) -> None:
        path = Path(words_file)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de palavras nao encontrado: {words_file}")
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw:
                continue

            if ";" in raw:
                theme, word = raw.split(";", 1)
            else:
                theme, word = "Geral", raw

            clean_word = word.strip().upper()
            clean_theme = theme.strip() or "Geral"
            if not clean_word:
                continue
            entries.append({"theme": clean_theme, "word": clean_word})

        if not entries:
            raise ValueError("Lista de palavras vazia")
        self.entries = entries

    def random_word(self) -> str:
        return self.random_entry()["word"]

    def random_entry(self, exclude_words: set[str] | None = None) -> dict:
        if exclude_words:
            pool = [entry for entry in self.entries if entry["word"] not in exclude_words]
            if not pool:
                pool = self.entries
            return random.choice(pool)
        return random.choice(self.entries)

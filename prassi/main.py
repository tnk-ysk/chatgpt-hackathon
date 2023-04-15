import os
import re
import time
import logging
from enum import Enum
from typing import Optional

import requests
import fire
import git
import openai
from openai.error import InvalidRequestError, RateLimitError

openai.api_key = os.getenv("OPENAI_API_KEY")


class Mode(Enum):
    origin = 1
    digest_test = 2
    digest_all = 3


def git_log(base) -> str:
    repo = git.Repo(".")
    log = repo.git.log(f"{base}..{repo.active_branch.name}", "--oneline")
    return log


def git_diff_files(base) -> list:
    repo = git.Repo(".")
    files = repo.git.diff("--merge-base", "--name-only", base).split("\n")
    return files


def git_diff(base, *args):
    repo = git.Repo(".")
    diff = repo.git.diff("--merge-base", base, *args)
    return diff


def git_diff_digest(base, model, *args) -> dict:
    digests = {}
    for f in args:
        diff = git_diff(base, f)
        print(f"Create diff digest: {f}")
        digests[f] = create_digest(model, "git diff", diff)
        print(f"diff digest {f}: {digests[f]}")
    return digests


def check_public_repo() -> bool:
    repo = git.Repo(".")
    url: str = repo.git.config("--get", "remote.origin.url")
    if url.startswith("git@"):
        url = re.sub("^git@([^:]+):(.+)$", r"https://\1/\2", url)
    print(f"giturl: {url}")

    url += "/info/refs?service=git-upload-pack"
    res = requests.get(url=url)
    print(f"public check status_code: {res.status_code}")

    return res.status_code == 200


def default_model():
    if "gpt-4" not in [x['id'] for x in openai.Model.list()['data']]:
        model = "gpt-3.5-turbo"
    else:
        model = "gpt-4"
    return model


def create_digest(model: str, desc: str, body: str, target: str = "内容", length: int = 200):
    res = None
    while res is None:
        messages = [
            {
                "role": "system",
                "content": (
                    "あなたは、あるgitリポジトリの熟練のcommitterで、そのリポジトリの内容を熟知しています。\n"
                    f"{desc}中の{target}を的確に要約することができます。\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"これは{desc}の内容です。\n\n"
                    f"{body}\n\n"
                    f"1行かつ{length}文字以下で要約してください。\n"
                )
            },
        ]
        try:
            res = open_ai_create(
                model=model,
                messages=messages,
                # temperature=1.0,
                # top_p=1.0,
            )
        except InvalidRequestError as e:
            m = re.search(r"maximum context length is (\d+).*your messages resulted in (\d+)", str(e))
            if m:
                rate = (int(m.group(1)) / int(m.group(2))) * 0.8
                body = body[:int(len(body) * rate)]
                print(f"Retry create {desc} digest. rate: {rate}, length: {len(body)}")
                continue
            else:
                raise e
    return res.choices[0].message.content.strip()


def judge_lang(model: str, body: str):
    res = None
    while res is None:
        messages = [
            {
                "role": "system",
                "content": (
                    "あなたは、英語や日本語などのあらゆる言語を扱うことができ、文章で使用されている言語が何かを判定することができます。\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"これに使用されているEnglishやJapaneseなどの言語を推定し、英語で1単語で回答してください。\n\n"
                    f"{body}"
                )
            },
        ]
        try:
            res = open_ai_create(
                model=model,
                messages=messages,
                temperature=0.5,
                # top_p=1.0,
            )
        except InvalidRequestError as e:
            m = re.search(r"maximum context length is (\d+).*your messages resulted in (\d+)", str(e))
            if m:
                rate = (int(m.group(1)) / int(m.group(2))) * 0.8
                body = body[:int(len(body) * rate)]
                print(f"Retry judge lang. rate: {rate}, length: {len(body)}")
                continue
            else:
                raise e
    return res.choices[0].message.content.strip()


def send(
        model: str,
        base: str,
        diff_files: list,
        readme: Optional[str],
        commit_log: str,
        lang: Optional[str],
        mode: Mode,
):
    res = None
    digests = None
    while res is None:
        if mode == Mode.origin:
            diff = git_diff(base)
            digests = None
        elif mode == Mode.digest_test:
            test_pattern = r'(?<![a-z])tests?(?![a-z])'
            sources = list(filter(lambda s: re.match(test_pattern, s, re.IGNORECASE) is None, diff_files))
            tests = list(filter(lambda s: re.match(test_pattern, s, re.IGNORECASE), diff_files))
            diff = git_diff(base, *sources) if len(sources) > 0 else None
            digests = git_diff_digest(base, model, *tests) if len(tests) > 0 else None
        elif mode == Mode.digest_all:
            diff = None
            digests = digests if digests is not None else {}
            f = set(diff_files).difference(set(digests.keys()))
            digests.update(git_diff_digest(base, model, *f))
        else:
            raise Exception(f"Unknown mode: {mode}")

        messages = [
            {
                "role": "system",
                "content": (
                    f"回答は必ず{lang}で行います、言語が異なるとコミュニケーションできないため絶対に守る必要があります。\n"
                    "あなたは、あるgitリポジトリの熟練のcommitterです。\n"
                    "そのリポジトリの内容を熟知しており、変更内容に沿った詳細かつ丁寧な説明のpull request用の文章を書くことができます。\n"
                    + (f'リポジトリのREADMEの要約は以下です。\n\n{readme}\n\n' if readme is not None else "")
                ),
            },
            {
                "role": "user",
                "content": (
                    (lang if lang is not None else "English") +
                    "で回答してください。\n\n" +
                    (f"これはbase branchとのdiffの内容です。\n\n{diff}\n\n" if diff else "") +
                    (
                        (f"その他にも変更があり" if diff is not None else "") +
                        f"変更内容の要約を以下の形式で記述します。\n\n"
                        "{ファイルパス}: {変更の要約}\n\n"
                        "これが変更の要約です。\n\n"
                        "".join(map(lambda x: f"{x[0]}: {x[1]}\n", digests.items())) +
                        "\n"
                        if digests is not None else ""
                    ) +
                    "これらのcommit logは以下です。\n\n"
                    f"{commit_log}\n\n"
                    "以上の内容をPRとして送りたいため、その説明文を書いてください。\n"
                    "説明文には概要、詳細な説明を含め、マークダウン形式で記述してください。\n"
                    "ただし、テストコードに関しては概要のみで構いません\n"
                    "既存のissueに紐づくcommit logがある場合は、そのissueへのリンクを含めてください\n\n"
                )
            },
        ]
        print(f"messages: {messages}")

        try:
            res = open_ai_create(
                model=model,
                messages=messages,
                temperature=0.5,
                # top_p=1.0,
            )
        except InvalidRequestError as e:
            m = re.search(r"maximum context length", str(e))
            if m:
                if mode == Mode.origin:
                    mode = Mode.digest_test
                elif mode == Mode.digest_test:
                    mode = Mode.digest_all
                else:
                    raise e
                print(f"Retry mode: {mode}")
                continue
            else:
                raise e
    print(f"result:\n{res.choices[0].message.content.strip()}")


def open_ai_create(**kwargs):
    res = None
    while res is None:
        try:
            res = openai.ChatCompletion.create(
                **kwargs
            )
        except RateLimitError as e:
            m = re.search(r"Please try again in (\d+)s", str(e))
            if m:
                sec = int(m.group(1)) + 5
                print(f"Rate limit exceeded, sleep {sec}s")
                time.sleep(sec)
                continue
            else:
                raise e
    return res


def main(base="origin/HEAD", model: str = None, safe: bool = True, digest_mode: str = "origin"):
    mode = Mode[digest_mode]
    if safe and not check_public_repo():
        raise Exception("git repo is not public")

    if model is None:
        model = default_model()

    diff_files = git_diff_files(base)
    if len(diff_files) == 0:
        raise Exception("No diffs")

    readme = None
    lang = None
    if os.path.exists("README.md"):
        with open("README.md", "r") as f:
            readme = f.read()
        readme = re.sub(r"<!--.*?-->\n?", "", readme, flags=re.DOTALL | re.MULTILINE)
        print(f"Check lang.")
        lang = judge_lang(model, readme)
        print(f"lang is {lang}")
        print(f"Create readme digest.")
        readme = create_digest(model, "README.md", readme, target="リポジトリの概要")
        print(f"readme digest: {readme}")

    commit_log = git_log(base)
    print(f"commit log: {commit_log}")
    send(model=model, base=base, diff_files=diff_files, readme=readme, commit_log=commit_log, lang=lang, mode=mode)


if __name__ == "__main__":
    fire.Fire(main)

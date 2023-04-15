import os
import re
import requests
import logging
import itertools

import fire
import git

import openai

openai.api_key = os.getenv("OPENAI_API_KEY")
THRESHOLD_TOKEN = 3500


def git_log(base) -> str:
    repo = git.Repo(".")
    log = repo.git.log(f"{base}..{repo.active_branch.name}", "--oneline")
    return log


def git_diff_files(base) -> list:
    repo = git.Repo(".")
    files = repo.git.diff("--merge-base", "--name-only", base).split("\n")
    return files


def create_diff_message(base, i, file):
    repo = git.Repo(".")
    diff = repo.git.diff("--merge-base", base, file)
    message = create_var_message(prefix="diff", i=i, message=diff)
    return message


def create_diff_messages(base):
    repo = git.Repo(".")
    diff = repo.git.diff("--merge-base", base)
    messages = create_var_messages(prefix="diff", message=diff)
    return messages


def create_var_messages(prefix, message: str):
    buff = ""
    messages = []
    i = 0
    for l in message.split("\n"):
        if len(buff) + len(l) > THRESHOLD_TOKEN * 4:
            messages.extend(create_var_message(prefix=prefix, i=i, message=buff))
            i = i + 1
            buff = ""
        buff += f"{l}\n"
    if len(buff) > 0:
        messages.extend(create_var_message(prefix=prefix, i=i, message=buff))
    return messages


def create_var_message(prefix, i, message):
    message = [
        {
            "role": "user",
            "content": (
                f"[{prefix}{i}]に以下の文字列を格納してください。\n\n"
                f"{message}"
            )
        },
        {
            "role": "assistant",
            "content": (
                f"了解しました。[{prefix}{i}]という変数に以下の文章を格納しました。"
            )
        }
    ]
    return message


def check_public_repo() -> bool:
    repo = git.Repo(".")
    url: str = repo.git.config("--get", "remote.origin.url")
    if url.startswith("git@"):
        url = re.sub("^git@([^:]+):(.+)$", "https://\\1/\\2", url)
    logging.info(f"giturl: {url}")

    url += "/info/refs?service=git-upload-pack"
    res = requests.get(url=url)
    logging.info(f"status_code: {res.status_code}")

    return res.status_code == 200


def default_model():
    if "gpt-4" not in [x['id'] for x in openai.Model.list()['data']]:
        model = "gpt-3.5-turbo"
    else:
        model = "gpt-4"
    return model


# def digest(model: str, body: str):


def send(model: str, base: str):
    with open("README.md", "r") as f:
        readme = f.read()
    readme_token = create_var_messages(prefix="rdm", message=readme)
    commit_log = git_log(base)
    # diff_files = git_diff_files(base)
    # diff_messages = list(itertools.chain.from_iterable(
    #     map(lambda x: create_diff_message(base=base, i=x[0], file=x[1]), enumerate(diff_files))
    # ))
    diff_messages = create_diff_messages(base)

    messages = [
        {
            "role": "system",
            "content": (
                "あなたは、あるgitリポジトリの熟練のcommitterです。\n"
                "そのリポジトリの内容を熟知しており、変更内容に沿った詳細かつ丁寧な説明のpull request用の文章を書くことができます。\n"
                # "リポジトリのREADMEの内容は以下です。\n\n"
                # f"{readme}"
            ),
        },
        *readme_token,
        *diff_messages,
        {
            "role": "user",
            "content": (
                f"前提\n\n"
                f"rdm0からrdm{len(readme_token)/2}を文字列結合したものは、このリポジトリのREADMEの内容です。\n"
                "リポジトリのREADMEの言語で回答してください。\n\n"
                f"diff0からdiff{len(diff_messages)/2}を文字列結合したものは、base branchとのdiffの内容です。\n\n"
                "これらの変更のcommit logは以下です。\n\n"
                f"{commit_log}\n\n"
                "この内容をPRとして送りたいため、その説明文を書いてください。\n"
                "説明文には概要、詳細な説明を含め、マークダウン形式で記述してください。\n"
                "ただし、テストコードに関しては重要な変更以外は概要のみで構いません\n"
                "commit logを参考に、既存のissueに関する修正が含まれている場合は、次のいずれかを使用した参照を含めてください。\n\n"
                "closes: #{issue_no}\n"
                "related: #{issue_no}\n"
            )
        },
    ]
    print(messages)
    logging.info(f"messages: {messages}")

    response = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        # temperature=1.0,
        # top_p=1.0,
    )

    logging.info(f"response: {response}")
    print(f"result:\n{response.choices[0].message.content.strip()}")


def main(base="origin/HEAD", model=None, check_public=True):
    if check_public and not check_public_repo():
        raise Exception("git repo is not public")

    if model is None:
        model = default_model()

    send(model=model, base=base)


if __name__ == "__main__":
    fire.Fire(main)

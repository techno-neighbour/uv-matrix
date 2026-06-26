import os

import dotenv

dotenv.load_dotenv("envfile")


def test_env():
    return os.environ["UV_MATRIX_TEST"]

from fast_engineer_mcp import __version__
from fast_engineer_mcp.cli import build_parser


def parse(*argv):
    return build_parser().parse_args(argv)


def test_default_command_is_serve():
    args = parse()
    assert args.command is None
    assert args.url is None and args.key is None
    assert args.anonymous is False and args.verbose is False


def test_flags_work_before_and_after_the_subcommand():
    assert parse("--url", "http://a/mcp").url == "http://a/mcp"
    assert parse("serve", "--url", "http://a/mcp").url == "http://a/mcp"
    assert parse("--key", "K", "check").key == "K"
    assert parse("check", "--key", "K", "--anonymous").anonymous is True
    assert parse("--verbose", "serve").verbose is True


def test_login_takes_its_own_key():
    args = parse("login", "--key", "abc")
    assert args.command == "login" and args.key == "abc"
    assert parse("login").key is None


def test_version(capsys):
    try:
        parse("--version")
    except SystemExit as exc:
        assert exc.code == 0
    assert __version__ in capsys.readouterr().out

from pathlib import Path

import pytest

from ripple.ingest.parser import ParsedBlock, parse_file


FIXTURE_ROOT = (Path(__file__).parent / "fixtures" / "sample_repo").resolve()
FIXTURE_FILES = (
    FIXTURE_ROOT / "main.tf",
    FIXTURE_ROOT / "variables.tf",
)


def _all_fixture_blocks() -> list[ParsedBlock]:
    return [
        block
        for file_path in FIXTURE_FILES
        for block in parse_file(file_path, FIXTURE_ROOT)
    ]


@pytest.mark.parametrize("file_path", FIXTURE_FILES)
def test_block_bodies_match_exact_source_lines(file_path: Path) -> None:
    source_lines = file_path.read_text().splitlines()

    for block in parse_file(file_path, FIXTURE_ROOT):
        expected_lines = source_lines[block.start_line - 1 : block.end_line]

        assert block.body.splitlines() == expected_lines
        assert block.body.splitlines()[0].startswith(block.block_kind)
        assert block.body.splitlines()[-1].strip() == "}"


def test_addresses_and_labels_are_correct() -> None:
    blocks = {block.address: block for block in _all_fixture_blocks()}

    assert set(blocks) == {
        "aws_security_group.worker",
        "data.aws_ami.ubuntu",
        "module.vpc",
        "var.region",
        "var.environment",
        "locals:variables.tf:11",
    }

    resource = blocks["aws_security_group.worker"]
    assert resource.block_kind == "resource"
    assert resource.resource_type == "aws_security_group"
    assert resource.resource_name == "worker"

    data_source = blocks["data.aws_ami.ubuntu"]
    assert data_source.block_kind == "data"
    assert data_source.resource_type == "aws_ami"
    assert data_source.resource_name == "ubuntu"

    module = blocks["module.vpc"]
    assert module.resource_type is None
    assert module.resource_name == "vpc"

    locals_block = blocks["locals:variables.tf:11"]
    assert locals_block.resource_type is None
    assert locals_block.resource_name is None


def test_heredoc_comment_and_string_braces_do_not_end_block_early() -> None:
    resource = parse_file(FIXTURE_ROOT / "main.tf", FIXTURE_ROOT)[0]

    assert resource.start_line == 1
    assert resource.end_line == 16
    assert 'name = "worker-\\"quoted\\"-{group}"' in resource.body
    assert "# This comment contains a misleading closing brace: }" in resource.body
    assert "policy = <<-EOF" in resource.body
    assert '      "Statement": [' in resource.body
    assert resource.body.splitlines()[-2].strip() == "EOF"
    assert resource.body.splitlines()[-1] == "}"


def test_nested_block_braces_do_not_end_parent_early() -> None:
    data_source = parse_file(FIXTURE_ROOT / "main.tf", FIXTURE_ROOT)[1]

    assert data_source.address == "data.aws_ami.ubuntu"
    assert data_source.start_line == 18
    assert data_source.end_line == 25
    assert "  filter {" in data_source.body
    assert data_source.body.splitlines()[-1] == "}"


def test_invalid_hcl_raises_value_error(tmp_path: Path) -> None:
    invalid_file = tmp_path / "invalid.tf"
    invalid_file.write_text(
        'resource "aws_instance" "broken" {\n'
        '  ami = "ami-123456"\n'
    )

    with pytest.raises(ValueError, match="not valid HCL"):
        parse_file(invalid_file, tmp_path)

from ripple.ingest import references


def test_extracts_plain_reference_with_trailing_attribute() -> None:
    body = "vpc_id = aws_vpc.main.id"

    assert references.extract_references(body) == ["aws_vpc.main.id"]


def test_extracts_data_source_reference() -> None:
    body = "ami = data.aws_ami.ubuntu.id"

    assert references.extract_references(body) == ["data.aws_ami.ubuntu.id"]


def test_does_not_include_surrounding_list_bracket() -> None:
    body = "security_groups = [aws_security_group.worker.id]"

    assert references.extract_references(body) == [
        "aws_security_group.worker.id"
    ]


def test_preserves_balanced_reference_index() -> None:
    body = "subnet_id = module.vpc.private_subnets[0]"

    assert references.extract_references(body) == [
        "module.vpc.private_subnets[0]"
    ]


def test_extracts_reference_with_index_then_attribute() -> None:
    body = "value = aws_instance.node[0].private_ip"

    assert references.extract_references(body) == [
        "aws_instance.node[0].private_ip"
    ]


def test_ignores_references_inside_comments() -> None:
    body = """
# aws_vpc.hash_comment.id
// aws_subnet.slash_comment.id
/*
data.aws_ami.block_comment.id
*/
vpc_id = aws_vpc.main.id
"""

    assert references.extract_references(body) == ["aws_vpc.main.id"]


def test_extracts_reference_inside_quoted_string() -> None:
    body = 'name = "${aws_vpc.main.id}-security-group"'

    assert references.extract_references(body) == ["aws_vpc.main.id"]


def test_extracts_reference_after_hash_inside_heredoc() -> None:
    body = """
policy = <<-EOF
# This is heredoc data: aws_iam_role.example.arn
EOF
"""

    assert references.extract_references(body) == ["aws_iam_role.example.arn"]


def test_preserves_multiple_references_and_duplicates_in_order() -> None:
    body = """
vpc_id = aws_vpc.main.id
subnet_id = aws_subnet.public.id
other_vpc_id = aws_vpc.main.id
"""

    assert references.extract_references(body) == [
        "aws_vpc.main.id",
        "aws_subnet.public.id",
        "aws_vpc.main.id",
    ]


def test_resolves_resource_and_data_source_addresses() -> None:
    assert references._resolve_reference_address("aws_vpc.main.id") == "aws_vpc.main"
    assert (
        references._resolve_reference_address("data.aws_ami.ubuntu.id")
        == "data.aws_ami.ubuntu"
    )
    assert references._resolve_reference_address("aws_vpc.main") == "aws_vpc.main"


def test_extracts_and_resolves_underscored_name_in_full() -> None:
    body = "policy = data.aws_iam_policy_document.dynamodb_endpoint_policy.json"

    extracted = references.extract_references(body)

    assert extracted == [
        "data.aws_iam_policy_document.dynamodb_endpoint_policy.json"
    ]
    assert references._resolve_reference_address(extracted[0]) == (
        "data.aws_iam_policy_document.dynamodb_endpoint_policy"
    )

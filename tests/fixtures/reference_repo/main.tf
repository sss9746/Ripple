resource "aws_vpc" "main" {
  cidr_block = var.cidr
}

resource "aws_subnet" "public" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

resource "aws_security_group" "worker" {
  vpc_id = aws_vpc.main.id

  ingress {
    security_groups = [aws_security_group.worker.id]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
}

resource "aws_instance" "node" {
  ami       = data.aws_ami.ubuntu.id
  subnet_id = aws_subnet.public.id
  iam_role  = aws_iam_role.missing.name

  tags = {
    Name = local.prefix
  }
}

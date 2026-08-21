resource "aws_security_group" "worker" {
  name = "worker-\"quoted\"-{group}"

  # This comment contains a misleading closing brace: }
  description = "Worker security group"

  policy = <<-EOF
    {
      "Statement": [
        {
          "Effect": "Allow"
        }
      ]
    }
  EOF
}

data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/*"]
  }
}

module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
}

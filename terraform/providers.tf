# Declares Terraform + AWS/archive providers. "aws.replica" is a second region for S3 replication only.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  backend "s3" {
    bucket         = "budget-analyzer-terraform-state-054041090724"
    key            = "budget-analyzer/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "budget-analyzer-terraform-state-lock"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

provider "aws" {
  alias  = "replica"
  region = var.replica_region

  default_tags {
    tags = local.common_tags
  }
}

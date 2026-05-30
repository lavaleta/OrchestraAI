terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.5.0"

  # Terraform state must be stored remotely for CI/CD pipelines
  backend "s3" {
    bucket = "orchestra-ai-terraform-state-demo-deploy"
    key    = "dev/terraform.tfstate"
    region = "eu-east-1"
    
    # Optional: create the DynamoDB lock table
    dynamodb_table = "terraform-lock"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "OrchestraAI"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# --- Module: Database (DynamoDB) ---
module "database" {
  source = "./modules/database"

  environment = var.environment
}

# --- Module: Queue (SQS) ---
module "queue" {
  source = "./modules/queue"

  environment = var.environment
}

# --- Module: Compute (Lambdas) ---
module "compute" {
  source = "./modules/compute"

  environment         = var.environment
  dynamodb_table_arn  = module.database.table_arn
  dynamodb_table_name = module.database.table_name
  sqs_queue_arn       = module.queue.queue_arn
  sqs_queue_url       = module.queue.queue_url
}

# --- Module: API Gateway ---
module "api_gateway" {
  source = "./modules/api_gateway"

  environment     = var.environment
  api_lambda_arn  = module.compute.api_lambda_arn
  api_lambda_name = module.compute.api_lambda_name
}
output "api_gateway_url" {
  description = "The public URL to access the FastAPI backend"
  value       = module.api_gateway.api_endpoint
}

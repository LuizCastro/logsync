variable "tenancy_ocid" {
  description = "OCID of the tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the user"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the API key"
  type        = string
}

variable "private_key_path" {
  description = "Path to the private key file"
  type        = string
  default     = "~/.oci/oci_api_key.pem"
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "sa-saopaulo-1"
}

variable "compartment_ocid" {
  description = "OCID of the compartment (leave empty to create a new one)"
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "SSH public key for compute instance"
  type        = string
}

variable "instance_shape" {
  description = "Shape of the compute instance"
  type        = string
  default     = "VM.Standard.E4.Flex"
}

variable "instance_ocpus" {
  description = "Number of OCPUs"
  type        = number
  default     = 2
}

variable "instance_memory_in_gbs" {
  description = "Memory in GBs"
  type        = number
  default     = 8
}

variable "n8n_password" {
  description = "Password for n8n basic auth"
  type        = string
  default     = "synapse2024"
}

variable "n8n_user" {
  description = "Username for n8n basic auth"
  type        = string
  default     = "synapse"
}

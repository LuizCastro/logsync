terraform {
  required_version = ">= 1.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# ── Compartment (create if not provided) ────────────────────
resource "oci_identity_compartment" "synapse" {
  count       = var.compartment_ocid == "" ? 1 : 0
  name        = "synapse-hackathon"
  description = "Compartment for Synapse Decision Intelligence"
}

locals {
  compartment_id = var.compartment_ocid != "" ? var.compartment_ocid : oci_identity_compartment.synapse[0].id
}

# ── VCN ─────────────────────────────────────────────────────
resource "oci_core_virtual_network" "synapse" {
  compartment_id = local.compartment_id
  display_name   = "synapse-vcn"
  cidr_block     = "10.0.0.0/16"
}

# ── Internet Gateway ────────────────────────────────────────
resource "oci_core_internet_gateway" "synapse" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_virtual_network.synapse.id
  display_name   = "synapse-igw"
  enabled        = true
}

# ── Route Table ─────────────────────────────────────────────
resource "oci_core_route_table" "synapse" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_virtual_network.synapse.id
  display_name   = "synapse-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.synapse.id
  }
}

# ── Security List ───────────────────────────────────────────
resource "oci_core_security_list" "synapse" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_virtual_network.synapse.id
  display_name   = "synapse-sl"

  # SSH
  ingress_security_rules {
    protocol = 6 # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # n8n
  ingress_security_rules {
    protocol = 6 # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      min = 5678
      max = 5678
    }
  }

  # Synapse API
  ingress_security_rules {
    protocol = 6 # TCP
    source   = "0.0.0.0/0"
    tcp_options {
      min = 8080
      max = 8080
    }
  }

  # All outbound
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

# ── Subnet ──────────────────────────────────────────────────
resource "oci_core_subnet" "synapse" {
  compartment_id      = local.compartment_id
  vcn_id              = oci_core_virtual_network.synapse.id
  display_name        = "synapse-subnet"
  cidr_block          = "10.0.1.0/24"
  route_table_id      = oci_core_route_table.synapse.id
  security_list_ids   = [oci_core_security_list.synapse.id]
  dhcp_options_id     = oci_core_virtual_network.synapse.default_dhcp_options_id
}

# ── Availability Domain ─────────────────────────────────────
data "oci_identity_availability_domains" "ads" {
  compartment_id = local.compartment_id
}

# ── Compute Instance ────────────────────────────────────────
resource "oci_core_instance" "synapse" {
  compartment_id      = local.compartment_id
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "synapse-server"
  shape               = var.instance_shape

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_in_gbs
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.oracle_linux.images[0].id
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.synapse.id
    display_name     = "synapse-vnic"
    assign_public_ip = true
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
      n8n_user     = var.n8n_user
      n8n_password = var.n8n_password
    }))
  }

  timeouts {
    create = "10m"
  }
}

# ── Oracle Linux Image ──────────────────────────────────────
data "oci_core_images" "oracle_linux" {
  compartment_id           = local.compartment_id
  operating_system         = "Oracle Linux"
  operating_system_version = "8"
  shape                    = var.instance_shape
}

# ── Output ──────────────────────────────────────────────────
output "instance_public_ip" {
  value = oci_core_instance.synapse.public_ip
}

output "instance_id" {
  value = oci_core_instance.synapse.id
}

output "compartment_id" {
  value = local.compartment_id
}

output "ssh_command" {
  value = "ssh -i ${var.private_key_path} ubuntu@${oci_core_instance.synapse.public_ip}"
}

output "n8n_url" {
  value = "http://${oci_core_instance.synapse.public_ip}:5678"
}

output "synapse_api_url" {
  value = "http://${oci_core_instance.synapse.public_ip}:8080"
}

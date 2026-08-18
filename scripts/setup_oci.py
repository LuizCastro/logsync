#!/usr/bin/env python3
"""
Synapse — OCI Generative AI Agent Setup
Creates the Knowledge Base, Agent, and Endpoint in OCI.

Prerequisites:
  - OCI CLI configured (`oci setup config`)
  - pip install oci
  - Set env vars: OCI_COMPARTMENT_ID, OCI_REGION

Usage:
    python setup_oci.py --compartment-id ocid1.compartment.oc1..xxx --region sa-saopaulo-1
"""

import oci
import argparse
import time
import json
import sys
from pathlib import Path


def create_knowledge_base(client, compartment_id, display_name="synapse-decisions"):
    """Create a Knowledge Base for storing decisions."""
    print(f"[1/4] Creating Knowledge Base '{display_name}'...")

    response = client.create_knowledge_base(
        create_knowledge_base_details=oci.generative_ai_agent.models.CreateKnowledgeBaseDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            description="Synapse Decision Intelligence — stores extracted decisions, rationale, and action items for cross-meeting context retrieval.",
            index_config=oci.generative_ai_agent.models.OciDatabaseConfig(
                index_config_type="OCI_DATABASE_CONFIG",
                database_connection=oci.generative_ai_agent.models.DatabaseToolConnection(
                    connection_type="DATABASE_TOOL_CONNECTION",
                    # You'll need to create an ATP connection first
                    connection_id="REPLACE_WITH_YOUR_ATP_CONNECTION_OCID"
                ),
                database_functions=[
                    oci.generative_ai_agent.models.DatabaseFunction(name="VECTOR_SEARCH")
                ]
            )
        )
    )

    kb_id = response.data.id
    print(f"  ✓ Knowledge Base created: {kb_id}")
    return kb_id


def create_agent(client, compartment_id, kb_id, display_name="synapse-agent"):
    """Create the Synapse Agent with RAG Tool."""
    print(f"[2/4] Creating Agent '{display_name}'...")

    system_instruction = """You are Synapse, a Decision Intelligence Engine.

Your job is to analyze meeting transcripts, Slack messages, and chat logs to extract STRUCTURED DECISIONS.

For each DECISION found, extract and return:
- title: Short imperative title (e.g., "Use PostgreSQL instead of MongoDB")
- decision: What was decided, in one clear sentence
- rationale: WHY this decision was made (the reasoning behind it)
- alternatives_rejected: What other options were considered and why rejected
- owner: Who is responsible for executing this decision (if mentioned)
- action_items: Concrete next steps that follow from this decision
- confidence: 0.0-1.0 how confident this is a REAL DECISION vs discussion
- related_topics: Keywords for linking to other decisions

RULES:
- A DECISION is a commitment to a specific course of action.
  "We should consider X" is NOT a decision. "We're going with X" IS a decision.
- Extract ONLY real decisions. If no decisions found, return empty array.
- Do NOT extract discussion, brainstorming, or status updates.
- Confidence < 0.5 = likely just discussion. Only return decisions with confidence >= 0.5.
- Be aggressive about action items — they die in the gap between meeting and tracker.
- When asked about past decisions, search the Knowledge Base for related context.
- Always return valid JSON with a "decisions" array.
- If the user asks for a daily brief, summarize all decisions from today with action items."""

    response = client.create_agent(
        create_agent_details=oci.generative_ai_agent.models.CreateAgentDetails(
            compartment_id=compartment_id,
            display_name=display_name,
            description="Synapse — Decision Intelligence Agent that extracts decisions from meetings and chat, maintains cross-meeting memory, and generates action plans.",
            welcome_message="Hi, I'm Synapse. I extract decisions from your meetings and chat, and keep them organized with action items. Send me a transcript or message to get started.",
            llm_config=oci.generative_ai_agent.models.LlmConfig(
                model="meta.llama-3.1-70b-instruct",
                temperature=0.1,
                max_tokens=2000,
            ),
            instruction=system_instruction,
        )
    )

    agent_id = response.data.id
    print(f"  ✓ Agent created: {agent_id}")
    return agent_id


def create_endpoint(client, agent_id, compartment_id, display_name="synapse-endpoint"):
    """Create an endpoint for the agent."""
    print(f"[3/4] Creating Endpoint '{display_name}'...")

    response = client.create_agent_endpoint(
        create_agent_endpoint_details=oci.generative_ai_agent.models.CreateAgentEndpointDetails(
            compartment_id=compartment_id,
            agent_id=agent_id,
            display_name=display_name,
            description="Synapse API endpoint for decision extraction",
        )
    )

    endpoint_id = response.data.id
    print(f"  ✓ Endpoint created: {endpoint_id}")

    # Wait for endpoint to become ACTIVE
    print("  ⏳ Waiting for endpoint to activate...")
    waiter = client.get_waiter(client, "agent_endpoint")
    waiter.wait(
        endpoint_id,
        "ACTIVE",
        wait_for_states=["ACTIVE", "FAILED"],
        timeout=300
    )

    # Get the actual endpoint URL
    endpoint_info = client.get_agent_endpoint(endpoint_id)
    endpoint_url = endpoint_info.data.service_endpoint
    print(f"  ✓ Endpoint URL: {endpoint_url}")
    return endpoint_id, endpoint_url


def create_data_source(client, kb_id, bucket_name, namespace, compartment_id):
    """Add Object Storage data source to the Knowledge Base."""
    print(f"[4/4] Creating Data Source from bucket '{bucket_name}'...")

    response = client.create_data_source(
        create_data_source_details=oci.generative_ai_agent.models.CreateObjectStorageDataSourceDetails(
            compartment_id=compartment_id,
            knowledge_base_id=kb_id,
            display_name="synapse-decisions-source",
            description="Meeting transcripts and Slack exports stored in Object Storage",
            bucket=namespace,
            prefix="decisions/",
            namespace=namespace,
        )
    )

    ds_id = response.data.id
    print(f"  ✓ Data Source created: {ds_id}")

    # Start ingestion job
    print("  ⏳ Starting data ingestion...")
    ingestion_response = client.create_data_ingestion_job(
        create_data_ingestion_job_details=oci.generative_ai_agent.models.CreateDataIngestionJobDetails(
            knowledge_base_id=kb_id,
            data_source_id=ds_id,
            display_name="synapse-initial-ingestion",
        )
    )
    print(f"  ✓ Ingestion job started: {ingestion_response.data.id}")
    return ds_id


def save_config(config_path, data):
    """Save the generated config for the n8n workflow."""
    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n📦 Config saved to {config_path}")
    print("   Use this in your n8n workflow to connect to OCI.")


def main():
    parser = argparse.ArgumentParser(description="Setup OCI resources for Synapse")
    parser.add_argument("--compartment-id", required=True, help="OCI Compartment OCID")
    parser.add_argument("--region", default="sa-saopaulo-1", help="OCI Region")
    parser.add_argument("--kb-name", default="synapse-decisions", help="Knowledge Base name")
    parser.add_argument("--agent-name", default="synapse-agent", help="Agent name")
    parser.add_argument("--bucket", help="Object Storage bucket name (for data source)")
    parser.add_argument("--namespace", help="Object Storage namespace")
    parser.add_argument("--atp-connection", help="ATP connection OCID for DB-backed KB")
    parser.add_argument("--dry-run", action="store_true", help="Print config without creating")
    args = parser.parse_args()

    # Initialize OCI client
    config = oci.config.from_file()
    config["region"] = args.region

    if args.dry_run:
        print("DRY RUN — would create:")
        print(f"  Knowledge Base: {args.kb_name}")
        print(f"  Agent: {args.agent_name}")
        print(f"  Endpoint: synapse-endpoint")
        print(f"  Region: {args.region}")
        print(f"  Compartment: {args.compartment_id}")
        return

    client = oci.generative_ai_agent.GenerativeAiAgentClient(config)

    # 1. Knowledge Base
    kb_id = create_knowledge_base(client, args.compartment_id, args.kb_name)

    # 2. Agent
    agent_id = create_agent(client, args.compartment_id, kb_id, args.agent_name)

    # 3. Endpoint
    endpoint_id, endpoint_url = create_endpoint(client, agent_id, args.compartment_id)

    # 4. Data Source (optional)
    ds_id = None
    if args.bucket and args.namespace:
        ds_id = create_data_source(client, kb_id, args.bucket, args.namespace, args.compartment_id)

    # Save config
    config_data = {
        "oci_region": args.region,
        "oci_compartment_id": args.compartment_id,
        "knowledge_base_id": kb_id,
        "agent_id": agent_id,
        "agent_endpoint_id": endpoint_id,
        "agent_endpoint_url": endpoint_url,
        "data_source_id": ds_id,
        "model": "meta.llama-3.1-70b-instruct",
    }

    save_config("data/oci-config.json", config_data)

    print("\n" + "=" * 60)
    print("✅ Synapse OCI setup complete!")
    print("=" * 60)
    print(f"\nNext steps:")
    print(f"  1. Install n8n community node: npm install n8n-nodes-oci-generative-ai")
    print(f"  2. Import the workflow from workflows/synapse-oci.json")
    print(f"  3. Configure credentials with your OCI API key")
    print(f"  4. Set agent_endpoint_id = {endpoint_id}")
    print(f"  5. Start the workflow!")


if __name__ == "__main__":
    main()

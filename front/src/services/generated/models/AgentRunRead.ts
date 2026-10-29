/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNodeRunRead } from './AgentNodeRunRead';
/**
 * Return durable state and safe binding metadata, excluding stored config internals and leases.
 */
export type AgentRunRead = {
    id: string;
    kind: 'workflow' | 'connection_test';
    agent_id: (string | null);
    agent_name: string;
    agent_version: number;
    model_id: string;
    model_name: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
    input_text: string;
    output_text: string;
    error: string;
    max_tokens: number;
    attempt: number;
    retry_of: (string | null);
    created_at: string;
    started_at: (string | null);
    finished_at: (string | null);
    nodes?: Array<AgentNodeRunRead>;
};


/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Keep concrete node inputs, outputs and failures next to the workflow, not task-center summaries.
 */
export type AgentNodeRunRead = {
    node_id: string;
    position: number;
    label: string;
    type: string;
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'skipped';
    input_text: string;
    output_text: string;
    prompt: string;
    parameters: Record<string, any>;
    usage: Record<string, any>;
    error: string;
    started_at: (string | null);
    finished_at: (string | null);
};


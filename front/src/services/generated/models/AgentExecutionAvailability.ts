/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExecutionModelChoice } from './ExecutionModelChoice';
/**
 * Separate configured text execution from image/video generation qualification.
 */
export type AgentExecutionAvailability = {
    available: boolean;
    message: string;
    default_model_id?: (string | null);
    models?: Array<ExecutionModelChoice>;
    max_llm_nodes?: number;
    call_kind?: 'agent_text' | 'model_connection_test';
};


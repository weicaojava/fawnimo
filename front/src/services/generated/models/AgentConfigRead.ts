/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNode } from './AgentNode';
import type { AgentParameters } from './AgentParameters';
import type { AgentTypeKey } from './AgentTypeKey';
/**
 * Return complete saved settings and real timestamps, without provider credentials.
 */
export type AgentConfigRead = {
    name: string;
    type: AgentTypeKey;
    description?: string;
    is_default?: boolean;
    is_favorite?: boolean;
    prompt?: string;
    temperature?: number;
    top_p?: number;
    parameters?: AgentParameters;
    workflow?: Array<AgentNode>;
    id: string;
    version: number;
    created_by: string;
    updated_by: string;
    created_at: string;
    updated_at: string;
};


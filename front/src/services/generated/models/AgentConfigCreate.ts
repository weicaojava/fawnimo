/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNode } from './AgentNode';
import type { AgentParameters } from './AgentParameters';
import type { AgentTypeKey } from './AgentTypeKey';
/**
 * Persist a complete Agent draft while rejecting hidden fields and broken graphs.
 */
export type AgentConfigCreate = {
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
};


/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNode } from './AgentNode';
import type { AgentParameters } from './AgentParameters';
import type { AgentTypeKey } from './AgentTypeKey';
/**
 * Allow partial metadata actions and full edits with an optional stale-version guard.
 */
export type AgentConfigUpdate = {
    expected_version?: (number | null);
    name?: (string | null);
    type?: (AgentTypeKey | null);
    description?: (string | null);
    is_default?: (boolean | null);
    is_favorite?: (boolean | null);
    prompt?: (string | null);
    temperature?: (number | null);
    top_p?: (number | null);
    parameters?: (AgentParameters | null);
    workflow?: (Array<AgentNode> | null);
};


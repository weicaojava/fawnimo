/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentConfigCreate } from './AgentConfigCreate';
/**
 * Portable configuration excludes database identity and never contains credentials.
 */
export type AgentConfigExport_Input = {
    schema_version?: number;
    config: AgentConfigCreate;
};


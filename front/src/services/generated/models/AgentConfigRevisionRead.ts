/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentConfigCreate } from './AgentConfigCreate';
/**
 * Expose saved versions for inspection or an explicit restore through normal update.
 */
export type AgentConfigRevisionRead = {
    version: number;
    saved_at: string;
    document: AgentConfigCreate;
};


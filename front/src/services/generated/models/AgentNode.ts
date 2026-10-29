/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Describe a local workflow node rather than executable code or arbitrary tools.
 */
export type AgentNode = {
    id: string;
    label: string;
    type: 'start' | 'llm' | 'end';
    prompt?: string;
    temperature?: number;
    top_p?: number;
};


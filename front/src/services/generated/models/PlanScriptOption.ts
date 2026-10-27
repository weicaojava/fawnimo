/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
import type { ScriptDocument } from './ScriptDocument';
/**
 * Latest chapter publication and its real current usability.
 */
export type PlanScriptOption = {
    id: string;
    content_hash: string;
    draft_revision: number;
    title: string;
    document: ScriptDocument;
    usable: boolean;
    issues: Array<PlanIssue>;
};


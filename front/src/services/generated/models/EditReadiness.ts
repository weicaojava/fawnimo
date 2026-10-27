/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditIssue } from './EditIssue';
/**
 * Software content readiness; never a semantic or model quality result.
 */
export type EditReadiness = {
    can_freeze: boolean;
    issues: Array<EditIssue>;
    human_declarations_only?: boolean;
};


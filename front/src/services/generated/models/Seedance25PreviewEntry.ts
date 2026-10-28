/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanIssue } from './PlanIssue';
/**
 * Keep each wire body attached to its original immutable shot-plan entry.
 */
export type Seedance25PreviewEntry = {
    shot_id: string;
    entry_hash: string;
    body: (Record<string, any> | null);
    issues: Array<PlanIssue>;
};


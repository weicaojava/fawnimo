/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanDocument_Output } from './PlanDocument_Output';
import type { PlanReadiness } from './PlanReadiness';
import type { PlanSummary } from './PlanSummary';
/**
 * Safe editable document and its prior compiled versions.
 */
export type PlanRead = {
    id: string;
    chapter_id: string;
    revision: number;
    document: PlanDocument_Output;
    readiness: PlanReadiness;
    plans: Array<PlanSummary>;
};


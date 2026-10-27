/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanDocument } from './PlanDocument';
/**
 * Save a replacement document at the revision shown to the user.
 */
export type PlanPatch = {
    expected_revision: number;
    document: PlanDocument;
};


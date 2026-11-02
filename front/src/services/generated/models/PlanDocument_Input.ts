/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanShotInput_Input } from './PlanShotInput_Input';
/**
 * Incomplete chapter preparation can be saved without a model account.
 */
export type PlanDocument_Input = {
    schema_version?: number;
    script_snapshot_id?: (string | null);
    model_id?: string;
    shots?: Array<PlanShotInput_Input>;
};


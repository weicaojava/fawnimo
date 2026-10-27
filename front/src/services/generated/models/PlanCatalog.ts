/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlanMediaOption } from './PlanMediaOption';
import type { PlanModelOption } from './PlanModelOption';
import type { PlanScriptOption } from './PlanScriptOption';
import type { PlanShotOption } from './PlanShotOption';
import type { PlanVersionOption } from './PlanVersionOption';
/**
 * Complete bounded chapter/project preparation choices.
 */
export type PlanCatalog = {
    script: (PlanScriptOption | null);
    shots: Array<PlanShotOption>;
    versions: Array<PlanVersionOption>;
    media: Array<PlanMediaOption>;
    models: Array<PlanModelOption>;
};


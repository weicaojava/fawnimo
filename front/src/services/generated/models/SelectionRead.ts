/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Return the original event on replay without reselecting its take.
 */
export type SelectionRead = {
    event_id: string;
    shot_id: string;
    take_id: string;
    revision: number;
    origin_kind: string;
    replayed?: boolean;
};


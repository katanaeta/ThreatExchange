# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

import bottle
import typing as t
from dataclasses import dataclass, asdict
from mypy_boto3_dynamodb.service_resource import Table

from hmalib import metrics
from hmalib.common.config import HMAConfig
from hmalib.common import config as hmaconfig
from hmalib.common.s3_adapters import ThreatExchangeS3PDQAdapter, S3ThreatDataConfig
from hmalib.common.configs.fetcher import ThreatExchangeConfig
from hmalib.common.threatexchange_config import (
    sync_privacy_groups,
    create_privacy_group_if_not_exists,
)

from hmalib.lambdas.api.middleware import (
    jsoninator,
    JSONifiable,
    DictParseable,
    SubApp,
)


@dataclass
class Dataset(JSONifiable):
    privacy_group_id: t.Union[int, str]
    privacy_group_name: str
    description: str
    fetcher_active: bool
    matcher_active: bool
    write_back: bool
    in_use: bool

    def to_json(self) -> t.Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Dataset":
        return cls(
            d["privacy_group_id"],
            d["privacy_group_name"],
            d["description"],
            d["fetcher_active"],
            d["matcher_active"],
            d["write_back"],
            d["in_use"],
        )

    @classmethod
    def from_collab(cls, collab: ThreatExchangeConfig) -> "Dataset":
        return cls(
            collab.privacy_group_id,
            collab.privacy_group_name,
            collab.description,
            collab.fetcher_active,
            collab.matcher_active,
            collab.write_back,
            collab.in_use,
        )


@dataclass
class SyncDatasetResponse(JSONifiable):
    response: str

    def to_json(self) -> t.Dict:
        return asdict(self)


@dataclass
class DeleteDatasetResponse(JSONifiable):
    response: str

    def to_json(self) -> t.Dict:
        return asdict(self)


@dataclass
class UpdateDatasetRequest(DictParseable):
    privacy_group_id: t.Union[int, str]
    fetcher_active: bool
    matcher_active: bool
    write_back: bool

    @classmethod
    def from_dict(cls, d: dict) -> "UpdateDatasetRequest":
        return cls(
            d["privacy_group_id"],
            d["fetcher_active"],
            d["matcher_active"],
            d["write_back"],
        )


@dataclass
class CreateDatasetRequest(DictParseable):
    privacy_group_id: t.Union[int, str]
    privacy_group_name: str
    description: str
    fetcher_active: bool
    matcher_active: bool
    write_back: bool

    @classmethod
    def from_dict(cls, d: dict) -> "CreateDatasetRequest":
        return cls(
            d["privacy_group_id"],
            d["privacy_group_name"],
            d["description"],
            d["fetcher_active"],
            d["matcher_active"],
            d["write_back"],
        )


@dataclass
class CreateDatasetResponse(JSONifiable):
    response: str

    def to_json(self) -> t.Dict:
        return asdict(self)


@dataclass
class ThreatExchangeDatasetSummary(Dataset):
    """
    Factual information about a ThreatExchange dataset. This could be
    information like the name of the privacy group, the type of content it
    covers, the number of hashes it has etc.

    At the same time, it is not meant to replace the Dataset type. It will *not*
    contain configs that the user can edit. Eg. writeback_active,
    fetcher_active. Those continue to stay in the Dataset super class.
    """

    hash_count: int
    match_count: int

    def to_json(self) -> t.Dict:
        dataset_json = super().to_json()
        dataset_json.update(hash_count=self.hash_count, match_count=self.match_count)

        return dataset_json


@dataclass
class DatasetSummariesResponse(JSONifiable):
    threat_exchange_datasets: t.List[ThreatExchangeDatasetSummary]

    def to_json(self) -> t.Dict:
        return {
            "threat_exchange_datasets": [
                dataset.to_json() for dataset in self.threat_exchange_datasets
            ]
        }


def _get_signal_hash_count_and_last_modified(
    threat_exchange_data_bucket_name: str,
    threat_exchange_data_folder: str,
) -> t.Dict[str, t.Tuple[int, str]]:
    # TODO this method is expensive some cache or memoization method might be a good idea.

    s3_config = S3ThreatDataConfig(
        threat_exchange_data_bucket_name=threat_exchange_data_bucket_name,
        threat_exchange_data_folder=threat_exchange_data_folder,
    )
    pdq_storage = ThreatExchangeS3PDQAdapter(
        config=s3_config, metrics_logger=metrics.names.api_hash_count()
    )
    pdq_data_files = pdq_storage.load_data()
    return {
        file_name.split("/")[-1].split(".")[0]: (
            len(rows),
            pdq_storage.last_modified[file_name],
        )
        for file_name, rows in pdq_data_files.items()
    }


def _get_threat_exchange_datasets(
    table: Table,
    threat_exchange_data_bucket_name: str,
    threat_exchange_data_folder: str,
) -> t.List[ThreatExchangeDatasetSummary]:
    collaborations = ThreatExchangeConfig.get_all()
    hash_counts: t.Dict[
        str, t.Tuple[int, str]
    ] = _get_signal_hash_count_and_last_modified(
        threat_exchange_data_bucket_name,
        threat_exchange_data_folder,
    )

    return [
        ThreatExchangeDatasetSummary(
            collab.privacy_group_id,
            collab.privacy_group_name,
            collab.description,
            collab.fetcher_active,
            collab.matcher_active,
            collab.write_back,
            collab.in_use,
            hash_count=t.cast(
                int,
                hash_counts.get(
                    collab.privacy_group_id,
                    [-1, ""],
                )[0],
            ),
            match_count=-1,  # fix will be based on new count system
        )
        for collab in collaborations
    ]


def get_datasets_api(
    hma_config_table: str,
    datastore_table: Table,
    threat_exchange_data_bucket_name: str,
    threat_exchange_data_folder: str,
) -> bottle.Bottle:
    """
    ToDo / FixMe: this file is probably more about privacy groups than datasets...
    """
    # The documentation below expects prefix to be '/datasets/'
    datasets_api = SubApp()
    HMAConfig.initialize(hma_config_table)

    @datasets_api.get("/", apply=[jsoninator])
    def get_all_dataset_summaries() -> DatasetSummariesResponse:
        """
        Returns summaries for all datasets. Summary includes all facts that are
        not configurable. Eg. its name, the number of hashes it has, the
        number of matches it has caused, etc.
        """
        return DatasetSummariesResponse(
            threat_exchange_datasets=_get_threat_exchange_datasets(
                datastore_table,
                threat_exchange_data_bucket_name,
                threat_exchange_data_folder,
            )
        )

    @datasets_api.post("/update", apply=[jsoninator(UpdateDatasetRequest)])
    def update_dataset(request: UpdateDatasetRequest) -> Dataset:
        """
        Update dataset values: fetcher_active, write_back, and matcher_active.
        """
        config = ThreatExchangeConfig.getx(str(request.privacy_group_id))
        config.fetcher_active = request.fetcher_active
        config.write_back = request.write_back
        config.matcher_active = request.matcher_active
        updated_config = hmaconfig.update_config(config).__dict__
        updated_config["privacy_group_id"] = updated_config["name"]
        return Dataset.from_dict(updated_config)

    @datasets_api.post("/create", apply=[jsoninator(CreateDatasetRequest)])
    def create_dataset(request: CreateDatasetRequest) -> CreateDatasetResponse:
        """
        Create a local dataset (defaults defined in CreateDatasetRequest)
        """
        assert isinstance(request, CreateDatasetRequest)

        create_privacy_group_if_not_exists(
            privacy_group_id=str(request.privacy_group_id),
            privacy_group_name=request.privacy_group_name,
            description=request.description,
            in_use=True,
            fetcher_active=request.fetcher_active,
            matcher_active=request.matcher_active,
            write_back=request.write_back,
        )

        return CreateDatasetResponse(
            response=f"Created dataset {request.privacy_group_id}"
        )

    @datasets_api.post("/sync", apply=[jsoninator])
    def sync_datasets() -> SyncDatasetResponse:
        """
        Fetch new collaborations from ThreatExchange and sync with the configs stored in DynamoDB.
        """
        sync_privacy_groups()
        return SyncDatasetResponse(response="Privacy groups are up to date")

    @datasets_api.post("/delete/<key>", apply=[jsoninator])
    def delete_dataset(key=None) -> DeleteDatasetResponse:
        """
        Delete the dataset with key=<key>
        """
        config = ThreatExchangeConfig.getx(str(key))
        hmaconfig.delete_config(config)
        return DeleteDatasetResponse(response="The privacy group is deleted")

    return datasets_api
